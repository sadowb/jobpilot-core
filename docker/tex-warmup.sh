#!/bin/sh
# Fills the Tectonic cache at image build time (see Dockerfile) so CV compilation runs offline (--only-cached).
# Tectonic fetches fonts lazily, so loading a font package is not enough: every font package that
# backend/src/jobpilot/domain/cv.py allows is also compiled as the document's default roman, sans and mono family,
# in regular, bold, italic and small caps.
set -eu
work="$(mktemp -d)"
cd "$work"
cp /tmp/tex-warmup/main.tex main.tex
tectonic -X compile --untrusted main.tex
# Font files are looked up per design size (ec-lmss17 for 17.28pt), so the sample covers every size command of the
# 10/11/12pt classes and every integer \fontsize from 5 to 26 pt, in each shape.
sizes=""
for n in 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 24 26; do
  sizes="$sizes {\\fontsize{$n}{$((n + 2))}\\selectfont Size $n}"
done
for pkg in lmodern helvet charter mathpazo roboto carlito cabin sourcesanspro tgheros tgtermes; do
  for family in rmdefault sfdefault ttdefault; do
    for pt in 10pt 11pt 12pt; do
      cat > "font.tex" <<TEX
\\documentclass[letterpaper,$pt]{article}
\\usepackage[T1]{fontenc}
\\usepackage[utf8]{inputenc}
\\usepackage{$pkg}
\\usepackage{textcomp}
\\renewcommand{\\familydefault}{\\$family}
\\begin{document}
\\tiny t \\scriptsize s \\footnotesize f \\small s \\normalsize n \\large l \\Large L \\LARGE LL \\huge h \\Huge H\\normalsize\\par
\\textbf{\\tiny t \\scriptsize s \\footnotesize f \\small s \\normalsize n \\large l \\Large L \\LARGE LL \\huge h \\Huge H}\\normalsize\\par
\\textit{\\tiny t \\scriptsize s \\footnotesize f \\small s \\normalsize n \\large l \\Large L \\LARGE LL \\huge h \\Huge H}\\normalsize\\par
\\textbf{\\textit{\\large bold italic}} \\textsc{small caps} \\textbullet{} 0123456789 \\&
\\par
$sizes
\\par\\textbf{$sizes}\\par\\textit{$sizes}
\\end{document}
TEX
      tectonic -X compile --untrusted font.tex >/dev/null 2>&1 || echo "warning: $pkg/$family/$pt did not compile" >&2
    done
  done
done
# the real thing again, offline: proves the shared preamble documents resolve from the cache alone
tectonic -X compile --untrusted --only-cached main.tex
cd /
rm -rf "$work"
