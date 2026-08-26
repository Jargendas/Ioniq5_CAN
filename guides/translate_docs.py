#!/usr/bin/env python3
"""Translate rendered text in .md and .tex files via Google Translate,
preserving all markup so the translated files still render correctly.

What is translated (the "rendered" text only):
  - Markdown: headings, paragraphs, list items, link labels, image alt text.
    Section links are updated too: each translated heading gets an explicit
    {#slug} id (matching GitHub and pandoc) and every [text](#old-slug)
    reference is rewritten to the new slug.
  - LaTeX: body prose, section titles, captions, \\item text, \\href display
    text, \\title/\\author, abstract text.
What is kept verbatim:
  - Markdown: code spans/fences, URLs, image paths, HTML tags, formatting.
  - LaTeX: preamble, every command, \\label/\\ref/\\cite, math, file paths,
    URLs, emails, \\includegraphics, bibliography, comments.

Workflow
--------
  1. Translate one or more files (or an entire tree) to a language:

       ./translate_docs.py --to fr guides/manuals/preconditioning_manual.tex
       ./translate_docs.py --to de --all
       ./translate_docs.py --to es -o /tmp/translations guides/manuals/*.tex guides/cars/*/*/*.md

  2. Sanity check: no Private-Use-Area placeholder characters should remain.

       rg -n $'\ue000-\uf8ff' preconditioning_manual.fr.tex   # expect no output

  3. Compile to PDF and inspect: add --compile to the translate command, or
     use --compile-only on files that are already translated:

       ./translate_docs.py --to fr --compile manuals/welcome_precon.tex
       ./translate_docs.py --compile-only --all

     .md files compile with `pandoc <file>.md -o <file>.pdf`, .tex files with
     `pdflatex`. LaTeX output requires the babel module for the target
     language (use --no-babel if it is missing).

  4. Spot check a few translated sections by eye; machine translation is
     not perfect.

Quick examples
--------------
     # free endpoint (no key needed)
     ./translate_docs.py --to de guides/manuals/preconditioning_manual.tex

     # paid API, key from env var
     GOOGLE_TRANSLATE_API_KEY=$KEY ./translate_docs.py --to es --all

     # paid API, key in a GPG-encrypted file
     echo -n "$KEY" | gpg -c -o api_key.gpg
     ./translate_docs.py --api-key-file api_key.gpg --to fr -o /tmp/translations guides/manuals/*.tex guides/cars/*/*/*.md

     # translate and compile to PDF in one go
     ./translate_docs.py --api-key-file api_key.gpg --to it --compile guides/manuals/welcome_precon.tex

     # just compile existing translated files, no translation
     ./translate_docs.py --compile-only --all

     # see what would be done without calling Google
     ./translate_docs.py --to nl --dry-run guides/manuals/preconditioning_manual.tex

Notes
-----
  * Defaults to the free/unofficial endpoint
    https://translate.googleapis.com/translate_a/single?client=gtx
    No API key required; keep --delay modest to avoid rate limits.
  * With --api-key, --api-key-file, or the GOOGLE_TRANSLATE_API_KEY
    environment variable the paid Google Cloud Translation API v2 endpoint
    https://translation.googleapis.com/language/translate/v2 is used instead.
    The request pins model=nmt (Neural Machine Translation), the
    price-optimized product: the first 500,000 characters/month are free and
    it costs $20/million characters after that (vs $80/M for Custom
    Translation and $25/M each way for Adaptive Translation).
    --api-key-file decrypts the key with `gpg -d FILE`, keeping it out of
    shell history. The paid endpoint has higher rate limits and counts against
    your billing quota.
  * Originals are never modified: output is written to
    basename.<lang><ext> next to the source (or into --out-dir).
  * Change detection: each translated output embeds a comment tagging the
    source file and the git commit it was translated from
    (`% translate_docs: FILE @ COMMIT` / `<!-- translate_docs: ... -->`).
    Re-running `--to` on an output whose source has not changed since that
    commit skips translation; `--compile-only` skips compiling a translated
    file whose embedded commit still matches its working-tree content.
    This requires a git repo; outside one, everything is always processed.
  * Only files tracked by git are processed: untracked files are skipped.
    All git-based features (tracking filter, change detection, embedded
    commit tags) require a git repo; outside one, only files explicitly
    listed as arguments are translated.
  * For .tex, if the target language is known to babel the preamble is patched
    to select that language (--no-babel disables this).
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

API_PAID = "https://translation.googleapis.com/language/translate/v2"
API_FREE = "https://translate.googleapis.com/translate_a/single"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) translate_docs"}
API_KEY_ENV = "GOOGLE_TRANSLATE_API_KEY"
PUA_START = 0xE000
PUA_END = 0xF8FF
PUA_RE = re.compile(r"[\ue000-\uf8ff]")
MAX_CHARS = 1500
MAX_ATTEMPTS = 3
BABEL = {
    "en": "english", "fr": "french", "de": "german", "es": "spanish",
    "it": "italian", "pt": "portuguese", "pt-br": "brazilian",
    "nl": "dutch", "pl": "polish", "ru": "russian", "zh-cn": "chinese",
    "ja": "japanese", "ko": "korean", "sv": "swedish", "da": "danish",
    "fi": "finnish", "cs": "czech", "tr": "turkish", "ro": "romanian",
    "el": "greek", "hu": "hungarian",
}

MD_RULES = [
    (r"`[^`\n]+`", re.M),                      # inline code
    (r"<[^>\n]+>", re.M),                      # html tags
    (r"!\[[^\]]*\]\([^)]+\)", re.M),           # images (whole)
    "links",                                   # protect link targets
    (r"\bhttps?://[^\s)\]>\"'<]+", re.M),      # bare urls
    (r"\bmailto:[^\s)\]>\"'<]+", re.M),        # email urls
    (r"\bwww\.[^\s)\]>\"'<]+", re.M),          # bare www
    (r"\{#[^}\n]*\}", 0),                      # header attributes / anchors
    (r"\\[^\s\w]", 0),                         # backslash escapes like \-
    (r"\*\*", 0), (r"__", 0), (r"~~", 0),      # emphasis markers
    (r"^#{1,6}[ \t]*", re.M),                  # atx headings
    (r"^[ \t]*(?:[-+*]|\d+[.)])[ \t]+", re.M), # list bullets / numbers
    (r"^[ \t]*(?:>[ \t]*)+", re.M),            # blockquotes
    (r"[ \t]{2,}$", re.M),                     # hard line breaks
]

TEX_RULES = [
    (r"%.*?$", re.M),                          # comments
    (r"\\begin\{verbatim\}.*?\\end\{verbatim\}", re.S),
    (r"\\begin\{(?:align|align\*|equation|equation\*|gather|gather\*|"
     r"multline|multline\*|displaymath|math)\}.*?\\end\{[a-zA-Z*]+\}", re.S),
    (r"\\begin\{[a-zA-Z*]+\}", 0),             # environment openers
    (r"\\end\{[a-zA-Z*]+\}", 0),               # environment closers
    (r"\\includegraphics(?:\[[^\]]*\])?\{[^{}]*\}", 0),
    (r"\\(?:label|ref|eqref|pageref|vref|Vref|autoref|cref|Cref|"
     r"cite|citet|Citep|parencite)\{[^{}]*\}", 0),
    (r"\\url\{[^{}]*\}", 0),
    (r"\\email\{[^{}]*\}", 0),
    (r"\\href\{[^{}]*\}", 0),                  # keep url, translate display text
    (r"\\texttt\{[^{}]*\}", 0),
    (r"\\verb\|[^|]*\|", 0),
    (r"\\bibliographystyle\{[^{}]*\}", 0),
    (r"\\bibliography\{[^{}]*\}", 0),
    (r"\$[^$\n]+\$", re.M),                    # inline math
    (r"\\\[.*?\\\]", re.S),                    # display math
    (r"\\\(.*?\\\)", re.S),                    # inline math
    (r"\\[^a-zA-Z]", 0),                       # escaped chars like \#
    (r"\\([a-zA-Z]+)\*?", 0),                  # command names
    (r"\[[!a-zA-Z]+\]", 0),                    # float specs / labels like [h]
    (r"[{}[\]]", 0),                           # braces / brackets
]


class Protector:
    """Replaces protected spans with unique Private-Use-Area placeholder
    characters. Single codepoints are used so adjacent placeholders can never
    share (and thus lose) a boundary character during translation."""

    def __init__(self):
        self.tokens = {}  # placeholder char -> original text

    def _ph(self, text):
        ch = chr(PUA_START + len(self.tokens))
        self.tokens[ch] = text
        return ch

    def protect(self, text, rules):
        for rule in rules:
            if rule == "links":
                text = re.sub(
                    r"\[([^\]]+)\]\(([^)]+)\)",
                    lambda m: "[" + m.group(1) + "]"
                              + self._ph("(" + m.group(2) + ")"),
                    text)
            else:
                pat, flags = rule
                text = re.sub(pat, lambda m: self._ph(m.group(0)), text,
                              flags=flags)
        return text

    def restore(self, text):
        for ch in sorted(self.tokens, key=ord, reverse=True):
            text = text.replace(ch, self.tokens[ch])
        return text


def gtx(text, source, target, api_key=None):
    """Translate `text` from `source` to `target`. With an API key the paid
    Google Cloud Translation API v2 is used; otherwise the free endpoint."""
    if api_key:
        params = {"q": text, "target": target, "format": "text",
                  "model": "nmt"}
        if source != "auto":
            params["source"] = source
        url = "{}?key={}&{}".format(
            API_PAID, urllib.parse.quote(api_key),
            urllib.parse.urlencode(params))
    else:
        params = {"client": "gtx", "sl": source, "tl": target,
                  "dt": "t", "q": text}
        url = "{}?{}".format(API_FREE, urllib.parse.urlencode(params))
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if api_key:
                return html.unescape(
                    data["data"]["translations"][0]["translatedText"])
            return "".join(seg[0] for seg in data[0])
        except Exception as exc:  # noqa: BLE001 - retry any transient failure
            last = exc
            time.sleep(1.0 * attempt)
    raise RuntimeError("Google Translate request failed: {}".format(last))


def _split(text):
    if len(text) <= MAX_CHARS:
        return [text]
    pieces, buf = [], ""
    for part in re.split(r"(?<=[.!?])\s+", text):
        if buf and len(buf) + 1 + len(part) > MAX_CHARS:
            pieces.append(buf)
            buf = part
        else:
            buf = (buf + " " + part) if buf else part
    if buf:
        pieces.append(buf)
    return pieces


def translate(text, source, target, delay, api_key=None):
    pieces = _split(text)
    out = []
    for i, piece in enumerate(pieces):
        out.append(gtx(piece, source, target, api_key=api_key))
        if i < len(pieces) - 1:
            time.sleep(delay)
    return " ".join(out)


def _has_letters(text):
    return re.search(r"[^\W\d_]", PUA_RE.sub("", text)) is not None


def translate_spans(protected, source, target, delay, api_key=None):
    """Translate only the pure-text spans of a protected block. Protected
    (PUA-placeholder) spans and punctuation-only spans are kept verbatim, so
    Google never sees markup and cannot drop or reorder it."""
    parts = re.split(r"([\ue000-\uf8ff]+)", protected)
    out = []
    for part in parts:
        if not part:
            continue
        if PUA_RE.fullmatch(part) or not _has_letters(part):
            out.append(part)
        else:
            lead = part[:len(part) - len(part.lstrip())]
            trail = part[len(part.rstrip()):]
            core = part.strip()
            out.append(lead + translate(core, source, target, delay,
                                        api_key=api_key) + trail)
    return "".join(out)


def translate_block(block, rules, source, target, delay, api_key=None):
    """Protect, translate, restore one block. Falls back to per-line
    translation if Google collapses/expands the number of newlines."""
    prot = Protector()
    protected = prot.protect(block, rules)
    if not _has_letters(protected):
        return block
    translated = translate_spans(protected, source, target, delay,
                                 api_key=api_key)
    if translated.count("\n") != block.count("\n"):
        lines = []
        for line in block.split("\n"):
            p = Protector()
            t = p.restore(translate_spans(p.protect(line, rules), source,
                                          target, delay, api_key=api_key))
            lines.append(t)
            time.sleep(delay)
        translated = "\n".join(lines)
    return prot.restore(translated)


HEADER_ATTR_RE = re.compile(r"\{#[^}]*\}\s*$")


def slugify(text):
    """GitHub-style slug: lower-case, keep runs of word characters, join with
    '-'. Matches pandoc's auto_identifiers for typical headings too."""
    return "-".join(re.findall(r"[\w]+", HEADER_ATTR_RE.sub("", text).lower(),
                               re.UNICODE))


def heading_lines(block):
    """Find ATX heading lines inside a (possibly multi-line) markdown block.
    Returns (line_index, level, heading_text) for each."""
    found = []
    for i, line in enumerate(block.split("\n")):
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if m:
            found.append((i, len(m.group(1)),
                          HEADER_ATTR_RE.sub("", m.group(2)).strip()))
    return found


def translated_heading_texts(block, line_indices):
    """Extract the translated heading texts from a translated block at the
    given (preserved) line positions."""
    lines = block.split("\n")
    texts = []
    for i in line_indices:
        if i >= len(lines):
            texts.append(None)
            continue
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", lines[i])
        texts.append(HEADER_ATTR_RE.sub("", m.group(2)).strip() if m else None)
    return texts


def build_slug_mapping(headings, trans_texts):
    """headings: (line_index, level, orig_text); trans_texts: parallel list.
    Returns (orig_slug -> trans_slug mapping, trans_slugs parallel list)."""
    ocount, tcount = {}, {}
    mapping, trans_slugs = {}, []
    for (_, _, orig_text), trans_text in zip(headings, trans_texts):
        o = slugify(orig_text)
        oc = ocount.get(o, 0)
        ocount[o] = oc + 1
        orig_slug = o if oc == 0 else "{}-{}".format(o, oc)
        if trans_text is None:
            trans_slugs.append(None)
            continue
        t = slugify(trans_text)
        tc = tcount.get(t, 0)
        tcount[t] = tc + 1
        trans_slug = t if tc == 0 else "{}-{}".format(t, tc)
        trans_slugs.append(trans_slug)
        mapping[orig_slug] = trans_slug
    return mapping, trans_slugs


def inject_header_ids(out_blocks, headings, trans_slugs):
    """Append explicit {#slug} attributes to translated headings that do not
    already carry one, so GitHub and pandoc both use the same identifier.
    headings: (block_index, line_index, orig_text)."""
    for (bi, li, _), ts in zip(headings, trans_slugs):
        if ts is None:
            continue
        lines = out_blocks[bi].split("\n")
        if li < len(lines) and not HEADER_ATTR_RE.search(lines[li]):
            lines[li] += " {#" + ts + "}"
            out_blocks[bi] = "\n".join(lines)


def replace_anchors(text, mapping):
    """Rewrite [text](#old-slug) references to the translated slugs. Longest
    slugs first and a non-slug lookahead avoid partial/prefix matches; a '{'
    lookbehind skips explicit {#id} attributes."""
    for old in sorted(mapping, key=len, reverse=True):
        text = re.sub(r"(?<![{])#" + re.escape(old) + r"(?![a-z0-9_\-])",
                      "#" + mapping[old], text)
    return text


def patch_babel(tex_text, lang):
    if lang.lower() not in BABEL:
        return tex_text
    name = BABEL[lang.lower()]
    tex_text = re.sub(
        r"\\usepackage(\[[^\]]*\])?\{babel\}",
        lambda m: "\\usepackage[" + name + "]{babel}",
        tex_text, count=1)
    tex_text = re.sub(
        r"\\definelanguagealias\{[a-zA-Z-]*\}\{[^{}]*\}",
        lambda m: "\\definelanguagealias{" + lang + "}{" + name + "}",
        tex_text, count=1)
    if re.search(r"\\selectlanguage\{" + re.escape(lang) + r"\}", tex_text) is None:
        tex_text = re.sub(
            r"\\begin\{document\}",
            lambda m: "\\begin{document}\n\\selectlanguage{" + lang + "}",
            tex_text, count=1)
    return tex_text


def translate_markdown(text, source, target, delay, update_anchors=True,
                       api_key=None):
    blocks = re.split(r"\n[ \t]*\n", text)
    headings, trans_texts, out = [], [], []
    for bi, block in enumerate(blocks):
        stripped = block.strip()
        if not stripped or re.match(r"^\s*(```|~~~)", stripped):
            out.append(block)
            continue
        hl = heading_lines(block)
        translated = translate_block(block, MD_RULES, source, target, delay,
                                     api_key=api_key)
        out.append(translated)
        if hl:
            for line_no, _, orig_text in hl:
                headings.append((bi, line_no, orig_text))
            trans_texts.extend(translated_heading_texts(
                translated, [l for l, _, _ in hl]))
    mapping = {}
    if update_anchors and headings:
        mapping, trans_slugs = build_slug_mapping(headings, trans_texts)
        inject_header_ids(out, headings, trans_slugs)
    result = "\n\n".join(out)
    if mapping:
        result = replace_anchors(result, mapping)
    return result


def translate_tex(text, source, target, delay, patch_babel_flag=True,
                  api_key=None):
    marker = "\\begin{document}"
    start = text.find(marker)
    if start == -1:
        raise ValueError("no \\begin{document} found (is this a .tex file?)")
    body = text[start:]
    body = "\n\n".join(
        translate_block(b, TEX_RULES, source, target, delay, api_key=api_key)
        for b in re.split(r"\n[ \t]*\n", body))
    result = text[:start] + body
    if patch_babel_flag:
        result = patch_babel(result, target)
    return result


def out_path(src, lang, out_dir):
    base = os.path.basename(src)
    stem, ext = os.path.splitext(base)
    name = stem + "." + lang + ext
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        return os.path.join(out_dir, name)
    return os.path.join(os.path.dirname(src) or ".", name)


def compile_pdf(src):
    """Compile a .md (pandoc) or .tex (pdflatex) file to PDF. Returns
    (ok, detail) where detail is the PDF path or an error message."""
    src = os.path.abspath(src)
    ext = os.path.splitext(src)[1].lower()
    d = os.path.dirname(src)
    base = os.path.basename(src)
    pdf = os.path.splitext(src)[0] + ".pdf"
    if ext == ".md":
        cmd = ["pandoc", base, "-o", os.path.basename(pdf)]
    elif ext == ".tex":
        jobname = os.path.splitext(base)[0]
        cmd = ["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
               "-jobname", jobname, "-output-directory", d, base]
    else:
        return False, "not a .md or .tex file"
    try:
        res = subprocess.run(cmd, cwd=d, capture_output=True, text=True)
    except FileNotFoundError:
        return False, "compiler not found (need pandoc for .md, pdflatex for .tex)"
    if res.returncode != 0:
        tail = (res.stdout + "\n" + res.stderr).strip().splitlines()[-15:]
        return False, "; ".join(tail)
    return True, pdf


def collect_all(include_translated=False):
    """Collect every .md/.tex under the current dir. When translating,
    already-translated files (e.g. manual.fr.tex) are skipped so they are not
    re-translated; when compiling everything, they are included."""
    found = []
    already = re.compile(r"\.([a-z]{2}(-[a-z]{2})?)\.(md|tex)$") \
        if not include_translated else None
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in (".git", ".ipynb_checkpoints",
                                                "__pycache__")]
        for fn in files:
            if fn.endswith((".md", ".tex")) and (
                    include_translated or already is None
                    or not already.search(fn)):
                found.append(os.path.join(root, fn))
    return sorted(found)


TAG_RE = re.compile(
    r"^(?:%|<!--) translate_docs: (\S+) @ ([0-9a-f]{40,})(?: -->)?\s*$",
    re.MULTILINE)


def _git(args):
    """Run a git subcommand in the script's working dir; None on failure."""
    try:
        return subprocess.run(["git"] + args, capture_output=True, text=True)
    except OSError:
        return None


def git_repo():
    """True if running inside a git work tree."""
    r = _git(["rev-parse", "--is-inside-work-tree"])
    return r is not None and r.returncode == 0 \
        and r.stdout.strip() == "true"


def git_commit_of(path):
    """Full commit hash that last touched `path` at HEAD, or None."""
    r = _git(["log", "-1", "--format=%H", "--", path])
    if r is None or r.returncode != 0:
        return None
    return r.stdout.strip() or None


def git_tracked(path):
    """True if `path` is tracked by git (in the index at HEAD)."""
    r = _git(["ls-files", "--error-unmatch", "--", path])
    return r is not None and r.returncode == 0


def git_unchanged_since(path, commit):
    """True if the working-tree content of `path` is identical to its state
    at `commit` (tracked, and no committed or uncommitted changes since)."""
    if not commit:
        return False
    r = _git(["ls-files", "--error-unmatch", "--", path])
    if r is None or r.returncode != 0:
        return False
    d = _git(["diff", "--quiet", commit, "--", path])
    return d is not None and d.returncode == 0


def git_unmodified(path):
    """True if `path` is tracked and has no uncommitted changes (its working
    tree matches HEAD)."""
    r = _git(["ls-files", "--error-unmatch", "--", path])
    if r is None or r.returncode != 0:
        return False
    d = _git(["diff", "--quiet", "HEAD", "--", path])
    return d is not None and d.returncode == 0


def read_tag(text):
    """Extract the embedded (src, commit) tag from translated text."""
    m = TAG_RE.search(text)
    return (m.group(1), m.group(2)) if m else None


def prepend_tag(ext, text, src, commit):
    """Prefix `text` with an invisible comment recording the source path and
    the git commit it was translated from."""
    if ext == ".md":
        return "<!-- translate_docs: {} @ {} -->\n\n{}".format(src, commit, text)
    return "% translate_docs: {} @ {}\n{}".format(src, commit, text)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Translate rendered text in .md/.tex files, preserving "
                    "markup.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("files", nargs="*", metavar="FILE",
                    help=".md or .tex files to translate")
    ap.add_argument("-t", "--to", metavar="LANG",
                    help="target language code, e.g. fr, de, es, pt-BR "
                         "(required unless --compile-only)")
    ap.add_argument("-s", "--from", dest="source", default="auto",
                    metavar="LANG", help="source language code (default auto)")
    ap.add_argument("-k", "--api-key", metavar="KEY",
                    help="Google Cloud Translation API key; overrides "
                         "$GOOGLE_TRANSLATE_API_KEY. Uses the paid v2 endpoint "
                         "instead of the free one.")
    ap.add_argument("--api-key-file", metavar="FILE",
                    help="GPG-encrypted file containing the API key; decrypted "
                         "with `gpg -d FILE` (the key is never written to "
                         "shell history)")
    ap.add_argument("-o", "--out-dir", metavar="DIR",
                    help="write translated files into DIR")
    ap.add_argument("--delay", type=float, default=0.3,
                    help="seconds between API calls (default 0.3)")
    ap.add_argument("--no-babel", action="store_true",
                    help="do not patch babel language lines in .tex output")
    ap.add_argument("--no-anchors", action="store_true",
                    help="do not update #anchor links or inject header ids "
                         "in .md output")
    ap.add_argument("--compile", action="store_true",
                    help="after translating, compile each output to PDF "
                         "(pandoc for .md, pdflatex for .tex)")
    ap.add_argument("--compile-only", action="store_true",
                    help="compile FILE(s) to PDF without translating")
    ap.add_argument("--all", action="store_true",
                    help="process every .md and .tex under the current dir "
                         "(for --compile-only this includes already-translated "
                         "files like manual.fr.tex)")
    ap.add_argument("--dry-run", action="store_true",
                    help="list inputs/outputs without calling Google Translate")
    args = ap.parse_args(argv)

    files = args.files
    if args.all:
        files = collect_all(include_translated=args.compile_only)
    if not files:
        ap.error("provide FILE(s) or use --all")

    before = len(files)
    if git_repo():
        files = [f for f in files if git_tracked(f)]
        if not files:
            ap.error("no git-tracked .md/.tex files to process")
        if len(files) < before:
            print("note: skipping {} untracked file(s); only files tracked "
                  "by git are processed".format(before - len(files)),
                  file=sys.stderr)
    elif not args.all:
        print("note: not in a git work tree; change detection and the "
              "git-tracking filter are disabled", file=sys.stderr)

    if args.compile_only:
        for src in files:
            tag = None
            if os.path.isfile(src):
                with open(src, encoding="utf-8") as fh:
                    tag = read_tag(fh.read())
            pdf = os.path.splitext(src)[0] + ".pdf"
            if (tag and os.path.isfile(pdf)
                    and (git_unchanged_since(src, tag[1])
                         or git_unmodified(src))):
                print("{} -> SKIP (unchanged since {})".format(
                    src, tag[1][:8]))
                continue
            ok, detail = compile_pdf(src)
            print("{} -> {}".format(src, "OK" if ok else "FAILED"))
            if not ok:
                print("  {}".format(detail))
        return 0

    if not args.to:
        ap.error("-t/--to is required for translation")

    api_key = args.api_key or os.environ.get(API_KEY_ENV)
    if args.api_key_file:
        try:
            res = subprocess.run(["gpg", "-d", args.api_key_file],
                                 capture_output=True, text=True, check=True)
            api_key = res.stdout.strip()
        except FileNotFoundError:
            ap.error("gpg not found on PATH (needed for --api-key-file)")
        except subprocess.CalledProcessError as exc:
            ap.error("gpg -d {} failed: {}".format(
                args.api_key_file, exc.stderr.strip() or exc))
    if api_key:
        print("using paid Google Cloud Translation API v2")

    for src in files:
        dst = out_path(src, args.to, args.out_dir)
        ext = os.path.splitext(src)[1].lower()
        if ext not in (".md", ".tex"):
            print("skip (not .md/.tex):", src, file=sys.stderr)
            continue
        print("{} -> {}".format(src, dst))
        if args.dry_run:
            continue
        if os.path.isfile(dst):
            with open(dst, encoding="utf-8") as fh:
                tag = read_tag(fh.read())
            if (tag and tag[0] == src
                    and git_unchanged_since(src, tag[1])):
                print("  unchanged since {}, skipping translation".format(
                    tag[1][:8]))
                if args.compile:
                    ok, detail = compile_pdf(dst)
                    print("  compile: {} -> {}".format(
                        "OK" if ok else "FAILED", detail))
                continue
        with open(src, encoding="utf-8") as fh:
            content = fh.read()
        if ext == ".md":
            result = translate_markdown(content, args.source, args.to,
                                        args.delay,
                                        update_anchors=not args.no_anchors,
                                        api_key=api_key)
        else:
            result = translate_tex(content, args.source, args.to, args.delay,
                                   patch_babel_flag=not args.no_babel,
                                   api_key=api_key)
        commit = git_commit_of(src)
        if commit:
            result = prepend_tag(ext, result, src, commit)
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(result)
        print("  wrote {} bytes".format(len(result.encode("utf-8"))))
        if args.compile:
            ok, detail = compile_pdf(dst)
            print("  compile: {} -> {}".format("OK" if ok else "FAILED", detail))

    return 0


if __name__ == "__main__":
    sys.exit(main())
