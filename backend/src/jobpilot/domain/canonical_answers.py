"""Canonical application questions: one fixed set of answer choices that many differently-worded form questions map to.

The owner picks each answer once (a dropdown on About me); the planner recognises the question by its wording and
translates the answer into whatever options the site offers. Pure rules, no IO."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .canonical import norm
from .countries import normalize_country

YES_NO = ("Yes", "No")
DECLINE = "Decline to answer"


@dataclass(frozen=True)
class Canonical:
    key: str
    title: str
    group: str
    options: tuple[str, ...]
    patterns: tuple[str, ...]
    contextual: bool = False  # answered from the job / work history, not from a dropdown the owner fills once


def _c(key: str, title: str, group: str, options: tuple[str, ...], *patterns: str, contextual: bool = False) -> Canonical:
    return Canonical(key, title, group, options, patterns, contextual)


REGISTRY: tuple[Canonical, ...] = (
    _c("work_authorization_us", "Authorized to work in the US", "Work eligibility", YES_NO,
       r"(authori[sz]ed|legally|eligible|permitted|right).*(work|employment).*\b(us|usa|united states|u s)\b", r"^work authori[sz]ation( status)?$"),
    _c("requires_sponsorship", "Needs visa sponsorship", "Work eligibility", YES_NO,
       r"(require|need).*(sponsorship|visa)", r"sponsorship.*(now|future|work)"),
    _c("us_person_status", "U.S. citizen / permanent resident / protected individual", "Work eligibility", YES_NO,
       r"\b(us|u s|united states) citizen", r"lawful permanent resident", r"protected individual", r"\bitar\b", r"export control"),
    _c("security_clearance", "Security clearance", "Work eligibility", ("None", "Confidential", "Secret", "Top Secret", "Other"),
       r"security clearance", r"clearance level"),
    _c("willing_relocate", "Willing to relocate", "Preferences", YES_NO, r"relocat"),
    _c("willing_onsite", "Willing to work on-site", "Preferences", YES_NO,
       r"(on site|onsite|in office|in person).*(willing|able|work)", r"(willing|able).*(on site|onsite|in office|in person)"),
    _c("willing_travel", "Willing to travel", "Preferences", YES_NO, r"(willing|able).*travel", r"travel.*(required|percent|up to)"),
    _c("seeking_internship", "Seeking an internship", "Preferences", YES_NO, r"seeking.*\bintern(ship)?s?\b", r"\bintern(ship)?s?\b.*(summer|seeking)", contextual=True),
    _c("age_18", "At least 18 years old", "Legal", YES_NO, r"(at least|over|older than|minimum age).*\b18\b(?! (months?|weeks?|days?)\b| years? of (experience|work))",
       r"\b18 years (of age|old)", r"\b18 or (older|over)"),
    _c("background_check", "Consent to a background check", "Legal", YES_NO, r"background (check|screening|investigation)"),
    _c("drug_test", "Consent to a drug test", "Legal", YES_NO, r"drug (test|screen)"),
    _c("non_compete", "Bound by a non-compete", "Legal", YES_NO, r"non ?compete", r"restrictive covenant", contextual=True),
    _c("previously_employed", "Previously worked at this company", "Legal", YES_NO,
       r"(previously|ever|formerly) (been )?(employed|worked) (by|at|for|with)\b", r"former employee", r"worked (here|for us)", contextual=True),
    _c("how_did_you_hear", "How you heard about the job", "Other",
       ("LinkedIn", "Company website", "Job board", "Referral", "Campus event", "Social media", "Other"),
       r"how did you (hear|find|learn)", r"where did you (hear|find|learn)", contextual=True),
    _c("education_level", "Highest level of education", "Other",
       ("High school", "Associate degree", "Bachelor's degree", "Master's degree", "Doctorate"),
       r"(highest|current) (level of )?(education|degree)", r"education level"),
    _c("gender", "Gender", "Voluntary self-identification", ("Male", "Female", "Non-binary", DECLINE),
       r"^gender( identity)?$", r"what is your gender", r"\bgender\b"),
    _c("hispanic_latino", "Hispanic or Latino", "Voluntary self-identification", ("Yes", "No", DECLINE), r"hispanic|latino|latinx"),
    _c("race_ethnicity", "Race / ethnicity", "Voluntary self-identification",
       ("American Indian or Alaska Native", "Asian", "Black or African American", "Native Hawaiian or Other Pacific Islander",
        "White", "Two or more races", DECLINE),
       r"\brace\b", r"ethnic"),
    _c("veteran_status", "Veteran status", "Voluntary self-identification",
       ("I am not a protected veteran", "I am a protected veteran", DECLINE), r"veteran"),
    _c("disability_status", "Disability status", "Voluntary self-identification",
       ("Yes, I have a disability", "No, I do not have a disability", DECLINE), r"disabilit"),
)  # fmt: skip

_BY_KEY = {c.key: c for c in REGISTRY}
_COMPILED = [(c, [re.compile(p) for p in c.patterns]) for c in REGISTRY]
_YES = {"yes", "y", "true", "i am", "i do", "i have"}
_NO = {"no", "n", "false", "i am not", "i do not", "i don t"}
_DECLINE = re.compile(r"decline|prefer not|do not wish|don t wish|choose not|not to (say|answer|disclose|identify)")
_STOP = {"i", "a", "an", "the", "of", "or", "and", "to", "am", "is", "are", "as", "in"}


def get(key: str) -> Canonical | None:
    return _BY_KEY.get(key)


def canonical_for(label: str) -> Canonical | None:
    text = norm(label)
    if not text:
        return None
    for item, patterns in _COMPILED:
        if any(p.search(text) for p in patterns):
            return item
    return None


def _tokens(text: str) -> set[str]:
    return {t for t in norm(text).split() if t not in _STOP}


_DIAL = re.compile(r"\s*\(?\+\d[\d\s-]*\)?\s*$")  # trailing dial code: "United States +1"


def choose_option(value: str, options: list[str], *, exact: bool = False) -> str | None:
    """The site's option that means the same as `value`, or None when no option clearly does."""
    if not options:
        return value
    target = norm(value)
    country = normalize_country(value)
    if country is not None:  # "USA" == "United States of America" == "United States"; phone pickers add "+1"
        same = [o for o in options if normalize_country(_DIAL.sub("", o)) == country]
        if same:
            return same[0]
    for option in options:
        if norm(option) == target:
            return option
    if exact:
        return None
    if target in _YES or target in _NO:
        want = "yes" if target in _YES else "no"
        starts = [o for o in options if norm(o).split(" ")[0] == want]
        if len(starts) == 1:
            return starts[0]
        contains = [o for o in options if re.search(rf"\b{want}\b", norm(o))]
        return contains[0] if len(contains) == 1 else None
    if target == "company website":
        sites = [o for o in options if re.search(r"career|company|website|direct|corporate|employer", norm(o))]
        if len(sites) == 1:
            return sites[0]
    if target == "linkedin":
        linked = [o for o in options if "linkedin" in norm(o)]
        if linked:
            return linked[0]
    if _DECLINE.search(target):
        declines = [o for o in options if _DECLINE.search(norm(o))]
        return declines[0] if len(declines) == 1 else None
    want_tokens = _tokens(value)
    best: tuple[float, str] | None = None
    for option in options:
        have = _tokens(option)
        if not have or not want_tokens:
            continue
        score = len(want_tokens & have) / len(want_tokens | have)
        if (want_tokens <= have or have <= want_tokens) and score < 0.5:  # whole words: "India" is not "Indiana"
            score = 0.5
        if best is None or score > best[0]:
            best = (score, option)
    return best[1] if best and best[0] >= 0.5 else None
