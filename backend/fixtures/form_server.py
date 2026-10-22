"""Local fake ATS for dry runs. Never talks to the internet. Run: python backend/fixtures/form_server.py"""

import html
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PORT = int(os.environ.get("FIXTURE_PORT", "4777"))
SUBMISSIONS = 0
SPA_SCRIPT = """<script>
// ?spa=1: Greenhouse-style submit: fetch, then replace the page in place and change the URL after a delay.
document.getElementById('f').addEventListener('submit', function (event) {
  event.preventDefault();
  fetch('/apply' + location.search, {method: 'POST', body: new FormData(event.target)}).then(function () {
    setTimeout(function () {
      history.pushState({}, '', '/thanks');
      document.body.innerHTML = '<h1>Thank you for applying.</h1><p>Your application has been submitted.</p>';
    }, 2500);
  });
});
</script>"""
CODE_PAGE = (
    "<!doctype html><title>Verify your email</title><h1>Verify your email address</h1>"
    "<p>Enter the 8-character security code we sent to fixture@example.invalid to finish your application.</p>"
    "<input id=code name=security_code maxlength=8 aria-label='Security code'><button type=button>Verify</button>"
)
ERROR_BANNER = (
    '<div role=alert style="border:2px solid #b00;padding:8px;margin:8px 0"><strong>There was a problem with your '
    "submission.</strong> Please correct the following: Phone number is not valid. Resume is required.</div>"
)

FORM = """<!doctype html><meta charset=utf-8><title>Fixture Co - Software Engineer Intern</title>
<body style="font-family:sans-serif;max-width:640px;margin:2rem auto">
<h1>Software Engineer Intern - Fixture Co</h1>
<p>Requisition FX-1042. Build things with Python, SQL and React. Remote.</p>
<form id=f method=post action="/apply" enctype="multipart/form-data">
<p><label for=first_name>First name *</label><br><input id=first_name name=first_name required></p>
<p><label for=last_name>Last name *</label><br><input id=last_name name=last_name required></p>
<p><label for=email>Email *</label><br><input id=email name=email type=email required></p>
<p><label for=phone>Phone</label><br><input id=phone name=phone type=tel></p>
<p><label for=linkedin>LinkedIn profile</label><br><input id=linkedin name=linkedin></p>
<p><label for=src>How did you hear about us? *</label><br><select id=src name=src required><option value="">Select...</option><option>LinkedIn</option><option>Company website</option><option>Other</option></select></p>
<fieldset><legend>Are you legally authorized to work in the United States? *</legend>
<label><input type=radio name=auth value=yes id=auth_yes required> Yes</label>
<label><input type=radio name=auth value=no id=auth_no> No</label></fieldset>
<p><label for=why>Why do you want to work here?</label><br><textarea id=why name=why></textarea></p>
<p><label for=resume>Resume *</label><br><input id=resume name=resume type=file required></p>
<p><button type=submit>Submit application</button></p></form>"""


WIDGET_SELECT = (
    "<p><label id=src_label>How did you hear about us? *</label><br>"
    "<input type=hidden id=src name=src required>"
    "<div id=src_widget role=combobox aria-labelledby=src_label aria-controls=src_list aria-expanded=false tabindex=0 "
    'style="border:1px solid #888;padding:6px;cursor:pointer" '
    """onkeydown="if(event.key==='Escape'){document.getElementById('src_list').hidden=true;this.setAttribute('aria-expanded','false')}" """
    "onclick=\"var l=document.getElementById('src_list');l.hidden=!l.hidden;this.setAttribute('aria-expanded',String(!l.hidden))\">"
    "<span id=src_value>Select...</span></div>"
    '<ul id=src_list role=listbox hidden style="border:1px solid #888;list-style:none;margin:0;padding:0">'
    + "".join(
        f'<li role=option tabindex=0 style="padding:6px;cursor:pointer" '
        f"onclick=\"document.getElementById('src').value='{o}';document.getElementById('src_value').textContent='{o}';"
        f"document.getElementById('src_list').hidden=true\">{o}</li>"
        for o in ("LinkedIn", "Company website", "Other")
    )
    + "</ul></p>"
)
WIDGET_SCHOOL = r"""
<p><label for=school>School *</label>
<input type=hidden id=school_value name=school>
<div class=choice-control onmousedown="openSchool()" onclick="openSchool()">
<span class=single-value id=school_selected></span>
<input id=school role=combobox aria-controls=school_list aria-autocomplete=list aria-required=true aria-expanded=false
  oninput="setTimeout(drawSchool,150)" onkeydown="if(event.key==='Escape'){closeSchool()}else if(event.key==='Backspace'&&!this.value){document.getElementById('school_selected').textContent='';document.getElementById('school_value').value=''}">
<ul id=school_list role=listbox hidden></ul></div></p>
<script>
function closeSchool(){document.getElementById('school_list').hidden=true;document.getElementById('school').setAttribute('aria-expanded','false')}
function openSchool(){document.getElementById('school').setAttribute('aria-expanded','true');drawSchool()}
function drawSchool(){
 const input=document.getElementById('school'), list=document.getElementById('school_list');
 list.replaceChildren();list.hidden=input.getAttribute('aria-expanded')!=='true';
 if(!input.value)return;
 const schools=['Fixture University','Fixture Other University','Other / not listed'];
 const matches=schools.filter(s=>s.toLowerCase()===input.value.toLowerCase());
 for(const text of matches.length?matches:['Fixture Other University','Other / not listed']){
   const option=document.createElement('li');option.setAttribute('role','option');option.textContent=text;
   option.onclick=()=>{document.getElementById('school_value').value=text;document.getElementById('school_selected').textContent=text;input.value='';closeSchool()};
   list.appendChild(option);
 }
}
</script>
"""
CONSENT_MODAL = (
    '<div id=consent role=dialog aria-modal=true aria-label="Cookie consent" '
    'style="position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;z-index:99">'
    '<div style="background:#fff;padding:24px;max-width:360px"><p>We use cookies to improve your experience.</p>'
    "<button type=button id=accept_cookies onclick=\"document.getElementById('consent').remove()\">Accept all</button></div></div>"
)


class H(BaseHTTPRequestHandler):
    def _send(self, body: str, code: int = 200) -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/count":
            return self._send(str(SUBMISSIONS))
        if urlsplit(self.path).path.startswith("/missing-404"):
            return self._send("<h1>Not found</h1>", 404)
        # ?t=<title> varies the role so dry runs can avoid the duplicate guard
        t = parse_qs(urlsplit(self.path).query).get("t", ["Software Engineer Intern"])[0][:80]
        query = parse_qs(urlsplit(self.path).query)
        page = FORM.replace("Software Engineer Intern", html.escape(t))
        suffix = urlsplit(self.path).query
        if suffix:  # variants (?slow=, ?code=, ?error=, ?spa=) must reach the POST handler
            page = page.replace('action="/apply"', f'action="/apply?{html.escape(suffix)}"')
        if query.get("spa", ["0"])[0] == "1":
            page += SPA_SCRIPT
        if parse_qs(urlsplit(self.path).query).get("widget", ["0"])[0] == "1":
            # ?widget=1: static dropdown + async school typeahead + blocking consent modal.
            page = re.sub(r"<p><label for=src>.*?</select></p>", lambda _m: WIDGET_SELECT + WIDGET_SCHOOL, page, flags=re.S)
            page = page.replace("<body ", CONSENT_MODAL + "<body ", 1) if "<body " in page else CONSENT_MODAL + page
        self._send(page)

    def do_POST(self) -> None:  # noqa: N802
        global SUBMISSIONS
        n = int(self.headers.get("content-length", "0"))
        self.rfile.read(n)
        query = parse_qs(urlsplit(self.path).query)
        delay = min(float(query.get("slow", ["0"])[0] or 0), 30.0)
        if delay:
            time.sleep(delay)  # the page keeps showing the form until the response lands
        if query.get("error", ["0"])[0] == "1":  # validation failure: nothing is recorded as submitted
            return self._send(FORM.replace("<form", ERROR_BANNER + "<form", 1))
        SUBMISSIONS += 1
        late = min(float(query.get("late", ["0"])[0] or 0), 120.0)
        if late:  # ?late=N: the confirmation replaces a "Submitting" notice only after N seconds
            return self._send(
                "<!doctype html><title>Submitting</title><p id=m>Submitting your application...</p><script>"
                f"setTimeout(function(){{document.getElementById('m').textContent='Thank you for applying. "
                f"We have received your application.'}},{int(late * 1000)})</script>"
            )
        if query.get("code", ["0"])[0] == "1":
            return self._send(CODE_PAGE)
        self._send("<!doctype html><h1>Thank you for applying</h1><p>We have received your application.</p>")

    def log_message(self, *a: object) -> None:
        pass


if __name__ == "__main__":
    print(f"fixture ATS on http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()  # Chrome keeps idle sockets open; one thread would hang
