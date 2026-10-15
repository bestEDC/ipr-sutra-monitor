"""The approved SUTRA alert design (BrandMaster Option A, signed off by Ian 2026-10-15).

Ported from mockups/design_build/build_sutra_ds.py. Markup, tokens and copy labels are the
approved ones; only the data plumbing changed. Do not restyle here; design changes go
through BrandMaster.

build() returns the HTML fragment that goes into the RSS item <description>.
"""
import html

# ---- tokens (InvestPR Email design system)
NAVY = "#1A1B4D"
ROYAL = "#003A7E"
BLUE = "#006EB9"
AQUA = "#D5F3F7"
GRAY100 = "#F0F0F0"
WHITE = "#FFFFFF"
HEADER_PURPLE = "#302457"
LIGHT_BLUE = "#98E1F8"
MINT = "#67DEC6"
MOV_PANEL, MOV_INK = MINT, NAVY

SANS = "Montserrat,Arial,Helvetica,sans-serif"
DISPLAY = "Teko,'Arial Narrow',Arial,sans-serif"

SEC_NEW = "New measures"
SEC_MOV = "Movement on tracked measures"
L_WHY = "Why it matters:"
L_TITLE = "Official title of the measure:"
L_KW = "Keywords found:"
L_NEWS = "What's new:"

FONT_LINK = ('<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800'
             '&amp;family=Teko:wght@300;400;500&amp;display=swap" rel="stylesheet" type="text/css">')

MSO_FONTS = ("<!--[if mso]><style type=\"text/css\">"
             ".ipr-sutra td,.ipr-sutra p,.ipr-sutra span,.ipr-sutra a,.ipr-sutra div{font-family:Arial,Helvetica,sans-serif !important;}"
             ".ipr-sutra .ipr-d{font-family:'Arial Narrow',Arial,sans-serif !important;}"
             "</style><![endif]-->")

# Scoped under .ipr-sutra so it cannot touch HubSpot's own template. HubSpot's RSS template
# forces every img to width:100% under 639px; the logo is pinned with one more class of specificity.
STYLE = """<style type="text/css">
.ipr-sutra img.ipr-logo,.hs_rss_email_entries_table .ipr-sutra img.ipr-logo{width:187px !important;max-width:187px !important;height:auto !important;}
@media only screen and (max-width:640px){
  .ipr-sutra .container{width:100% !important;max-width:100% !important;}
  .ipr-sutra .px{padding-left:24px !important;padding-right:24px !important;}
  /* one left edge on phones too: header text, tab and cards all start 16px in */
  .ipr-sutra .hdr{padding-left:16px !important;padding-right:16px !important;}
  .ipr-sutra .panel{padding-left:16px !important;padding-right:16px !important;}
  .ipr-sutra .tab-row{padding-left:16px !important;}
  .ipr-sutra .card-pad{padding-left:18px !important;padding-right:18px !important;}
  .ipr-sutra .body{font-size:14px !important;line-height:21px !important;}
  .ipr-sutra .fine{font-size:13px !important;line-height:19px !important;}
  .ipr-sutra .meta{display:block !important;width:100% !important;text-align:left !important;}
  .ipr-sutra .meta-date{padding:2px 0 0 !important;}
  .ipr-sutra .cta-table{width:100% !important;}
  .ipr-sutra .cta-cell{display:block !important;width:100% !important;box-sizing:border-box !important;margin:0 0 10px 0 !important;}
  .ipr-sutra .cta-a{display:block !important;}
  .ipr-sutra .cta-gap{display:none !important;}
  .ipr-sutra .bar-cell{display:block !important;width:100% !important;box-sizing:border-box !important;}
  .ipr-sutra .kw-l,.ipr-sutra .kw-r{display:block !important;width:100% !important;padding:0 0 6px !important;}
}
</style>"""


def e(s):
    return html.escape(s, quote=False)


def ea(s):
    return html.escape(s, quote=True)


def label(text, gap=6):
    return (f'<div class="ipr-d" style="font-family:{DISPLAY};font-size:17px;line-height:20px;font-weight:400;'
            f'color:{BLUE};text-transform:uppercase;margin:0 0 {gap}px;">{e(text)}</div>')


def body(text, extra=""):
    return (f'<div class="body" style="font-family:{SANS};font-size:12px;line-height:18px;font-weight:500;'
            f'color:{NAVY};{extra}">{text}</div>')


def _btn_text(text, href, ink, pad):
    return (f'<a class="cta-a" href="{ea(href)}" style="display:block;padding:{pad};font-family:{SANS};font-size:10px;'
            f'line-height:12px;font-weight:500;letter-spacing:.3em;text-transform:uppercase;color:{ink};'
            f'text-align:center;text-decoration:none;">{e(text)}</a>')


def button_bar(links):
    """The links are the card's bottom edge: SUTRA outline, document filled navy. One link spans the card."""
    width = 100 // len(links)
    cells = []
    for n, (text, href) in enumerate(links):
        fill, ink = (NAVY, WHITE) if n else (WHITE, NAVY)
        cells.append(
            f'<td class="bar-cell" width="{width}%" bgcolor="{fill}" style="width:{width}%;background:{fill};border-top:1px solid {NAVY};'
            f'mso-padding-alt:15px 0 15px 4px;">{_btn_text(text, href, ink, "15px 0 15px 4px")}</td>')
    return (f'<tr><td style="padding:0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="width:100%;"><tr>{"".join(cells)}</tr></table></td></tr>')


def keyword_footer(keywords):
    """Keyword-only chips, deduplicated in first-seen order, on one line with the label."""
    chips = " ".join(
        f'<span style="display:inline-block;background:{WHITE};border:solid {WHITE};border-width:2px 6px;'
        f'margin:0 0 4px;font-family:{SANS};font-size:10px;line-height:14px;font-weight:600;color:{NAVY};'
        f'white-space:nowrap;vertical-align:middle;">{e(t)}</span>'
        for t in dict.fromkeys(keywords))
    inner = (f'<div style="line-height:22px;">'
             f'<span class="ipr-d" style="font-family:{DISPLAY};font-size:16px;line-height:18px;font-weight:400;color:{BLUE};'
             f'text-transform:uppercase;white-space:nowrap;vertical-align:middle;margin-right:6px;">{e(L_KW)}</span> '
             f'{chips}</div>')
    return f'<tr><td class="card-pad" bgcolor="{GRAY100}" style="background:{GRAY100};padding:11px 28px 7px;">{inner}</td></tr>'


def card(item, movement=False):
    """item: num, date, headline, why, links[(text, href)], plus title + keywords (new) or news (movement)."""
    head = f"""<tr><td class="card-pad" bgcolor="{GRAY100}" style="background:{GRAY100};padding:9px 28px 6px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td class="meta ipr-d" style="vertical-align:middle;font-family:{DISPLAY};font-size:24px;line-height:26px;font-weight:400;color:{ROYAL};text-transform:uppercase;white-space:nowrap;">{e(item["num"])}</td>
<td class="meta meta-date" align="right" style="vertical-align:middle;text-align:right;padding:0 0 3px 12px;font-family:{SANS};font-size:11px;line-height:15px;font-weight:600;color:{NAVY};">{e(item["date"])}</td>
</tr></table></td></tr>"""
    parts = [
        f'<div style="font-family:{SANS};font-size:16px;line-height:21px;font-weight:700;color:{NAVY};margin:0 0 16px;">{e(item["headline"])}</div>',
        label(L_WHY),
        body(e(item["why"]), "margin:0 0 16px;"),
    ]
    if movement:
        parts += [label(L_NEWS), body(e(item["news"]), "font-weight:600;")]
    else:
        parts += [label(L_TITLE),
                  f'<div class="fine" lang="es" style="font-family:{SANS};font-size:11px;line-height:17px;font-weight:500;color:{NAVY};">{e(item["title"])}</div>']
    rows = [head,
            f'<tr><td class="card-pad" style="padding:22px 28px 24px;">{"".join(parts)}</td></tr>',
            button_bar(item["links"])]
    if not movement:
        rows.append(keyword_footer(item["keywords"]))
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{WHITE}" '
            f'style="background:{WHITE};">\n' + "\n".join(rows) + "\n</table>")


def section(title, items, movement=False, ground=WHITE, panel=AQUA, ink=ROYAL):
    """A tab rising out of whatever sits above it, then the panel of cards in the same colour."""
    gap = ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
           '<td height="12" style="height:12px;font-size:0;line-height:0;">&nbsp;</td></tr></table>')
    cards = gap.join(card(i, movement) for i in items)
    return f"""<tr><td class="tab-row" bgcolor="{ground}" style="background:{ground};padding:0 41px;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
<td class="ipr-d" bgcolor="{panel}" style="background:{panel};border-radius:8px 8px 0 0;padding:6px 22px 2px;font-family:{DISPLAY};font-size:20px;line-height:24px;font-weight:500;color:{ink};text-transform:uppercase;white-space:nowrap;">{e(title)}</td>
</tr></table></td></tr>
<tr><td class="panel" bgcolor="{panel}" style="background:{panel};padding:24px 41px 30px;">{cards}</td></tr>"""


def build(new, mov, *, title, subline, logo_url):
    """Each section is left out when it has no entries."""
    rows = [f"""<tr><td class="hdr" bgcolor="{HEADER_PURPLE}" style="background:{HEADER_PURPLE};padding:34px 41px 30px;">
<a href="https://www.investpr.org" style="text-decoration:none;"><img src="{ea(logo_url)}" width="187" alt="Invest Puerto Rico" class="ipr-logo" style="display:block;border:0;width:187px;height:auto;"></a>
<div class="ipr-d" style="font-family:{DISPLAY};font-size:37px;line-height:34px;font-weight:400;color:{WHITE};text-transform:uppercase;margin:28px 0 10px;">{e(title)}</div>
<div style="font-family:{SANS};font-size:12px;line-height:17px;font-weight:600;color:{LIGHT_BLUE};">{e(subline)}</div>
</td></tr>"""]
    if new:
        rows.append(section(SEC_NEW, new, ground=HEADER_PURPLE))
    if mov:
        rows.append(section(SEC_MOV, mov, movement=True, ground=AQUA if new else HEADER_PURPLE,
                            panel=MOV_PANEL, ink=MOV_INK))
    rows.append('<tr><td height="36" style="height:36px;font-size:0;line-height:0;">&nbsp;</td></tr>')
    inner = "\n".join(rows)
    return f"""{FONT_LINK}
{MSO_FONTS}
{STYLE}
<div class="ipr-sutra" style="margin:0 auto;max-width:650px;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="650" align="center" class="container" bgcolor="{WHITE}" style="width:650px;max-width:650px;background:{WHITE};margin:0 auto;">
{inner}
</table>
</div>"""
