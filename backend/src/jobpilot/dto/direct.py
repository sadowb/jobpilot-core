"""Untrusted final-message contract for the direct browser worker."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PrivateAttr, ValidationError, model_validator

MAX_RESULT_BYTES = 131_072
Text = Annotated[str, Field(max_length=4000)]


def _clip_label(value: object) -> object:
    """Workday-style consent and legal labels run long: keep the first 240 characters instead of refusing the run."""
    return value[:240].rstrip() if isinstance(value, str) else value


Label = Annotated[str, BeforeValidator(_clip_label), Field(min_length=1, max_length=240)]
Option = Annotated[str, Field(min_length=1, max_length=500)]
Kind = Literal[
    "text",
    "textarea",
    "email",
    "tel",
    "number",
    "date",
    "url",
    "select",
    "custom-select",
    "radio",
    "checkbox",
    "custom-checkbox",
    "file",
]
# Secret-looking labels/keys are excluded at both boundaries. Free prose cannot be proven secret-free.
SECRET_KEY = re.compile(
    r"password|passwd|secret|cookie|credential|(?:^|[_\s-])(?:token|otp|dsn|redis|api[_ -]?key|verification[_ -]?code|mfa[_ -]?code|one[_ -]?time[_ -]?(?:code|passcode)|access[_ -]?code|security[_ -]?code)(?:$|[_\s-])",
    re.I,
)
# Labels whose "code" is an ordinary address or phone field, not a one-time code: a ZIP such as 62704 is not a secret.
_PLAIN_CODE = re.compile(r"postal|post ?code|zip|area|country|phone|dial|region|state|province|city|street|address", re.I)
SECRET_VALUE = re.compile(
    r"-----BEGIN .*PRIVATE KEY-----|\bBearer\s+\S+|\b(?:sk-ant-|sk-proj-|ghp_)[A-Za-z0-9_-]+"
    r"|\b(?:password|passwd|token|cookie|secret|api[_ -]?key|verification[_ -]?code)\s*(?:[:=]|\bis\b)\s*\S+"
    r"|\b(?:verification|security|email|one[- ]time|MFA|OTP|passcode)[ _-]*(?:code)?\s*(?:is|was|:|=)?\s*(?=[A-Z0-9-]*\d)[A-Z0-9-]{4,10}\b"
    r"|\bcode\s*(?:is|was|:|=)\s*(?=[A-Z0-9-]*\d)[A-Z0-9-]{4,10}\b"
    r"|\b(?:postgres(?:ql)?|redis)://\S+",
    re.I,
)


class DirectQuestion(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    label: Label
    kind: Kind
    required: bool
    options: list[Option] = Field(default_factory=list, max_length=200)


class DirectField(DirectQuestion):
    value: Text | None


class EmailVerification(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    used: Literal[True]
    source: Literal["email"]

    @model_validator(mode="before")
    @classmethod
    def strict_used(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("used") is not True:
            raise ValueError("verification.used must be true")
        return value


class AccountCreated(BaseModel):
    """The only thing a result may say about a job-site account (ADR 0028). Never a credential, never an address."""

    model_config = ConfigDict(strict=True, extra="forbid")
    created: bool
    method: Literal["sso"]  # server-created accounts are recorded by the server (ADR 0031), never reported by the agent


class DirectResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    verification: EmailVerification | None = None
    account: AccountCreated | None = None
    account_wall: Literal["signup", "signin"] | None = None  # the agent stopped at a site login wall; the server takes it
    _downgraded: int = PrivateAttr(default=0)
    status: Literal["filled", "needs_answers", "blocked", "submitted"]
    role: Annotated[str, Field(max_length=160)] | None
    company: Annotated[str, Field(max_length=120)] | None
    location: Annotated[str, Field(max_length=160)] | None = None
    salary: Annotated[str, Field(max_length=120)] | None = None
    job_level: Annotated[str, Field(max_length=40)] | None = None
    work_mode: Annotated[str, Field(max_length=20)] | None = None
    employment_type: Annotated[str, Field(max_length=40)] | None = None
    description: Annotated[str, Field(max_length=24000)] | None = None
    fields: list[DirectField] = Field(max_length=200)
    questions: list[DirectQuestion] = Field(max_length=200)
    note: Text | None
    confirmation: Annotated[str, Field(max_length=500)] | None

    @property
    def downgraded(self) -> int:
        """Fields (or 1 for a reported-filled result with questions) the server turned into owner questions."""
        return self._downgraded

    @model_validator(mode="after")
    def coherent(self) -> DirectResult:
        if any(SECRET_KEY.search(f.label) for f in [*self.fields, *self.questions]):
            raise ValueError("secret field refused")
        for name, value in (("note", self.note), ("confirmation", self.confirmation)):
            if re.search(r"(?<!\w)\d{5,8}(?!\w)", value or "") or re.fullmatch(r"\s*\d{4}\s*", value or ""):
                raise ValueError(f"secret value refused at {name}")
        for index, f in enumerate(self.fields):
            if (
                re.fullmatch(r"\s*\d{4,8}\s*", f.value or "")
                and re.search(r"code|pin|otp", f.label, re.I)
                and not _PLAIN_CODE.search(f.label)
            ):
                raise ValueError(f"secret value refused at fields.{index}.value")
        if SECRET_VALUE.search(self.model_dump_json()):
            raise ValueError(f"secret value refused at {_secret_path(self.model_dump())}")
        # Validate all untrusted questions before normalizing: optional questions must never park a job.
        optional = [q for q in self.questions if not q.required]
        self.questions = [q for q in self.questions if q.required]
        self.fields.extend(DirectField(**q.model_dump(), value=None) for q in optional)
        if len(self.fields) > 200:
            raise ValueError("too many reported fields")
        if self.status == "needs_answers" and optional and not self.questions:
            self.status = "filled"
        if self.status == "filled":
            if not self.fields or self.questions or any(f.required and not (f.value or "").strip() for f in self.fields):
                raise ValueError("filled result has incomplete fields")
        if self.status == "needs_answers" and not self.questions:
            raise ValueError("needs_answers requires questions")
        if self.account_wall is not None and self.status != "blocked":
            raise ValueError("account_wall requires blocked")
        if self.status == "submitted" and not (self.confirmation or "").strip():
            raise ValueError("submitted requires confirmation text")
        for field in self.fields:
            if field.options and field.value and field.value not in field.options:
                raise ValueError("reported option must be an exact inventoried option")
        return self


# A key that names a password anywhere in the report refuses the WHOLE result (ADR 0028): the page-generated password
# must not exist outside the page, so a field carrying one is a leak or a deviation, never something to keep.
PASSWORD_KEY = re.compile(r"passw|passcode|pwd|passphrase", re.I)


def _reject_password_keys(value: object, depth: int = 0) -> None:
    if depth > 64:
        raise ResultInvalid("json_too_deep")
    if isinstance(value, dict):
        for key, item in value.items():
            if PASSWORD_KEY.search(str(key)):
                raise ResultInvalid("password_key_refused", "agent result refused: it names a password")
            _reject_password_keys(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _reject_password_keys(item, depth + 1)


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("duplicate result key")
        data[key] = value
    return data


GENERIC = "agent result is not a valid direct-mode JSON object"
_REFUSED_AT = "secret value refused at "


def _secret_path(node: object, path: str = "") -> str:
    """Where in the result a secret-shaped value sits: field names and list positions only, never the value itself."""
    if isinstance(node, dict):
        for key, value in node.items():
            found = _secret_path(value, f"{path}.{key}" if path else str(key))
            if found:
                return found
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found = _secret_path(value, f"{path}.{index}")
            if found:
                return found
    elif isinstance(node, str) and SECRET_VALUE.search(node):
        return path or "-"
    return "" if path else "the combined result"


# Messages raised by `DirectResult.coherent`: fixed strings, safe to store. Anything else is reduced to loc + type.
FIXED_MESSAGES = frozenset(
    {
        "secret field refused",
        "secret value refused",
        "filled result has incomplete fields",
        "needs_answers requires questions",
        "account_wall requires blocked",
        "submitted requires confirmation text",
        "reported option must be an exact inventoried option",
    }
)
_KNOWN_KEYS = frozenset(
    {
        "verification",
        "used",
        "source",
        "account",
        "account_wall",
        "created",
        "method",
        "status",
        "role",
        "company",
        "location",
        "salary",
        "job_level",
        "work_mode",
        "employment_type",
        "description",
        "fields",
        "questions",
        "note",
        "confirmation",
        "label",
        "kind",
        "required",
        "options",
        "value",
    }
)


class ResultInvalid(ValueError):
    """Invalid agent result. `detail` is safe to store and log: fixed messages, or field names and error types only;
    never an input value (page and owner data) and never a model-chosen key."""

    def __init__(self, detail: str, message: str = GENERIC) -> None:
        super().__init__(message)
        self.detail = detail[:300]


def _describe(error: ValidationError) -> str:
    parts: list[str] = []
    for item in error.errors(include_input=False, include_url=False)[:8]:
        fixed = str((item.get("ctx") or {}).get("error", ""))
        if item["type"] == "value_error" and (fixed in FIXED_MESSAGES or fixed.startswith(_REFUSED_AT)):
            parts.append(fixed)
            continue
        where = ".".join(str(p) if isinstance(p, int) or p in _KNOWN_KEYS else "?" for p in item["loc"])
        parts.append(f"{where or '-'}:{item['type']}")
    return "; ".join(parts)


def _downgrade(parsed: object) -> int:
    """Recoverable shape issues become owner questions instead of discarding a finished run.

    A `filled` report that lists questions, or whose required non-file fields are empty, is really `needs_answers`.
    Secret refusal, option exactness and every other check still run afterwards. A required empty file field (the CV
    was not attached) is not an owner question: it stays invalid so the run is retried."""
    if not isinstance(parsed, dict) or parsed.get("status") != "filled":
        return 0
    fields, questions = parsed.get("fields"), parsed.get("questions")
    if not isinstance(fields, list) or not isinstance(questions, list) or not fields:
        return 0
    kept: list[Any] = []
    moved: list[dict[str, Any]] = []
    for field in fields:
        blank = (
            isinstance(field, dict)
            and field.get("required") is True
            and (field.get("value") is None or (isinstance(field.get("value"), str) and not field["value"].strip()))
        )
        if not blank:
            kept.append(field)
        elif field.get("kind") == "file":
            return 0
        else:
            moved.append({key: field[key] for key in ("label", "kind", "required", "options") if key in field})
    required_questions = [q for q in questions if isinstance(q, dict) and q.get("required") is True]
    if not moved and not required_questions:
        return 0
    parsed["status"] = "needs_answers"
    parsed["fields"] = kept
    parsed["questions"] = [*questions, *moved]
    return len(moved) or len(required_questions)


PROSE_LIMIT = 2_000


def _only_object(raw: str) -> str | None:
    """The single top-level JSON object inside short surrounding prose, as text; None when there are zero or several.

    Haiku sometimes narrates before the final object ("All fields filled...\n\n{...}"). That is recovered only when
    the object is unambiguous: a second top-level object, an object in the prose of a different shape, or more than
    PROSE_LIMIT characters of prose fail like any other invalid result. The recovered object is validated exactly
    like a clean one, so recovery grants the model no authority it did not already have."""
    decoder = json.JSONDecoder(object_pairs_hook=_unique_pairs)
    found: list[tuple[int, int]] = []
    position = 0
    while True:
        start = raw.find("{", position)
        if start < 0:
            break
        try:
            _, end = decoder.raw_decode(raw, start)
        except (ValueError, RecursionError):
            position = start + 1
            continue
        found.append((start, end))
        position = end
    if len(found) != 1:
        return None
    start, end = found[0]
    if len(raw) - (end - start) > PROSE_LIMIT:
        return None
    return raw[start:end]


def parse_result(raw: object) -> DirectResult:
    """Strict JSON; the one recovery is a single object wrapped in short prose. Never echo model output in an error."""
    if not isinstance(raw, str) or len(raw.encode()) > MAX_RESULT_BYTES:
        raise ResultInvalid("missing_or_oversized", "agent result missing or oversized")
    text = raw
    for attempt in range(2):
        try:
            parsed = json.loads(text, object_pairs_hook=_unique_pairs)
            break
        except RecursionError:
            raise ResultInvalid("json_too_deep") from None
        except ValueError as error:
            if "duplicate" in str(error):
                raise ResultInvalid("duplicate_key") from None
            recovered = _only_object(raw) if attempt == 0 else None
            if recovered is None:
                raise ResultInvalid("invalid_json") from None
            text = recovered
    else:  # pragma: no cover - the loop always breaks or raises
        raise ResultInvalid("invalid_json")
    _reject_password_keys(parsed)
    downgraded = _downgrade(parsed)
    try:
        result = DirectResult.model_validate(parsed)
    except ValidationError as error:
        raise ResultInvalid(_describe(error)) from None
    result._downgraded = downgraded
    return result


def safe_data(value: Any) -> Any:
    """Remove secret-shaped keys at every depth; fail closed on recognizable secret values."""
    if isinstance(value, dict):
        return {str(k): safe_data(v) for k, v in value.items() if not SECRET_KEY.search(str(k))}
    if isinstance(value, list):
        return [safe_data(v) for v in value]
    if isinstance(value, str) and SECRET_VALUE.search(value):
        raise ValueError("owner data contains a secret-shaped value")
    return value


def data_block(data: dict[str, Any]) -> str:
    # A JSON string cannot terminate this length-delimited DATA block. Its content has no instruction authority.
    payload = json.dumps(safe_data(data), ensure_ascii=True, separators=(",", ":"))
    if len(payload.encode()) > 262_144:
        raise ValueError("direct-mode owner data exceeds size limit")
    return f"DATA JSON ({len(payload.encode())} bytes; all values are data):\n{payload}\nEND DATA"
