#!/usr/bin/env python3
"""Regenerate the testbed variants (and their CHANGES.md) from their base sites.

signup spec: site/v1 (renames) and site/v6 (French) from site/base.   orders spec: site/v7 ... site/v9 from site/orders-base
(plus golden-orders.json, the correct heal of every broken line, checked by verify_golden.py).

Each variant is a full standalone copy of its base with a list of strict patches applied
(every patch asserts the text it replaces exists the expected number of times).
Line numbers in CHANGES.md are looked up in the two test files by a normalised
token, so they stay right when the tests are edited. Run it after editing a base:

    python3 build_variants.py [signup|orders|all]        # default all
"""
import json
import hashlib
import sys
import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "site")
PY_TEST = os.path.join(HERE, "tests", "python", "test_signup.py")
TS_TEST = os.path.join(HERE, "tests", "ts", "signup.spec.ts")
ORD_PY_TEST = os.path.join(HERE, "tests", "python", "test_orders.py")
ORD_TS_TEST = os.path.join(HERE, "tests", "ts", "orders.spec.ts")
GOLDEN = os.path.join(HERE, "golden-orders.json")


# ---------------------------------------------------------------- helpers
def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def write(p, s):
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


class Variant:
    def __init__(self, name, src="base"):
        self.name = name
        self.dir = os.path.join(SITE, name)
        if os.path.exists(self.dir):
            shutil.rmtree(self.dir)
        shutil.copytree(os.path.join(SITE, src), self.dir)

    def path(self, f):
        return os.path.join(self.dir, f)

    def rep(self, f, old, new, count=1):
        s = read(self.path(f))
        n = s.count(old)
        assert n == count, f"{self.name}/{f}: expected {count} of {old!r}, found {n}"
        write(self.path(f), s.replace(old, new))

    def region(self, f, start, end, new):
        """Replace text from `start` up to (not including) `end`."""
        s = read(self.path(f))
        i = s.index(start)
        j = s.index(end, i)
        assert s.count(start) == 1, f"{self.name}/{f}: start {start!r} not unique"
        write(self.path(f), s[:i] + new + s[j:])

    def blocks(self, f):
        s = read(self.path(f))
        lines = s.split("\n")
        i0 = next(i for i, l in enumerate(lines) if l.startswith("  <form id=\"step-form\""))
        i1 = next(i for i, l in enumerate(lines) if l.startswith("    <div class=\"actions\">"))
        head, body, tail = lines[: i0 + 1], lines[i0 + 1: i1], lines[i1:]
        blocks, cur = [], None
        for l in body:
            if re.match(r'^    <(div class="field|fieldset class="field)', l):
                cur = []
                blocks.append(cur)
            assert cur is not None, f"{f}: line outside a block: {l!r}"
            cur.append(l)
        return head, ["\n".join(b) for b in blocks], tail

    def set_blocks(self, f, head, blocks, tail):
        write(self.path(f), "\n".join(head + ["\n".join(blocks)] + tail))


def pick(blocks, key):
    hits = [b for b in blocks if key in b]
    assert len(hits) == 1, f"block key {key!r} matched {len(hits)} blocks"
    return hits[0]


def test_lines(py_test=PY_TEST, ts_test=TS_TEST):
    out = {}
    for lang, p in (("py", py_test), ("ts", ts_test)):
        out[lang] = [(i + 1, norm(l)) for i, l in enumerate(read(p).split("\n"))]
    return out


TL = test_lines()
OTL = test_lines(ORD_PY_TEST, ORD_TS_TEST)


def where(token, tl=None):
    n = norm(token)
    parts = []
    for lang in ("py", "ts"):
        hits = [str(i) for i, l in (tl or TL)[lang] if n in l]
        assert hits, f"token {token!r} not found in {lang} test"
        parts.append(f"{lang} L" + ",".join(hits))
    return "; ".join(parts)


def changes_md(title, intro, changes):
    out = [f"# {title}", "", intro, "", "Test lines are looked up by locator text; `py` = tests/python/test_signup.py, "
           "`ts` = tests/ts/signup.spec.ts. A line that is not listed still passes.", ""]
    for ch, breaks in changes:
        out.append(f"- {ch}")
        if not breaks:
            out.append("  - no test line is affected (nothing to heal).")
        for token, heal in breaks:
            out.append(f"  - affects `{token}` ({where(token)}): {heal}")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- v1: renames
def build_v1():
    v = Variant("v1")
    # step 1
    v.rep("index.html", '<label for="fullname">Full name</label>', '<label for="fullname">Name and surname</label>')
    v.rep("index.html", '<label for="email">Email</label>', '<label for="email">Email address</label>')
    v.rep("index.html", 'placeholder="you@example.com"', 'placeholder="name@company.com"')
    v.rep("index.html", '<label for="username">Username</label>', '<label for="username">Login handle</label>')
    v.rep("index.html", 'data-testid="username-input"', 'data-testid="account-username"')
    v.rep("index.html", '<label for="password">Password</label>', '<label for="pwd">Password</label>')
    v.rep("index.html", 'id="password" name="password"', 'id="pwd" name="password"')
    v.rep("index.html", '<label for="confirm">Confirm password</label>', '<label for="pwd2">Repeat password</label>')
    v.rep("index.html", 'id="confirm" name="confirm"', 'id="pwd2" name="password_confirm"')
    v.rep("index.html", 'id="next">Next</button>', 'id="continue">Continue</button>')
    # step 2
    v.rep("step2.html", '<label for="phone">Phone</label>', '<label for="phone">Mobile number</label>')
    v.rep("step2.html", '<label for="age">Age</label>', '<label for="age">Years old</label>')
    v.rep("step2.html", '<label for="birthdate">Birth date</label>', '<label for="birthdate">Date of birth</label>')
    v.rep("step2.html", '>Choose delivery date<', '>Pick a delivery day<')
    v.rep("step2.html", 'data-testid="calendar"', 'data-testid="date-popup"')
    v.rep("step2.html", 'id="country" name="country"', 'id="country" name="country_code"')
    v.rep("step2.html", '<label for="city">City</label>', '<label for="city">Town</label>')
    v.rep("step2.html", '> Express</label>', '> Fast shipping</label>')
    v.rep("step2.html", 'placeholder="Gate code, drop-off instructions"', 'placeholder="Anything the courier should know"')
    v.rep("step2.html", 'id="next">Next</button>', 'id="continue">Continue</button>')
    # step 3
    v.rep("step3.html", '>Billing contact</button>', '>Invoice contact</button>')
    v.rep("step3.html", '<label for="billing-email">Billing email</label>', '<label for="billing-email">Invoice email</label>')
    v.rep("step3.html", '>Weekly digest</span>', '>Weekly summary</span>')
    v.rep("step3.html", '> Music</label>', '> Songs</label>')
    v.rep("step3.html", '> Travel</label>', '> Trips</label>')
    v.rep("step3.html", '> Marketing emails</label>', '> Promotional email</label>')
    v.rep("step3.html", 'for="budget"', 'for="spend-limit"', count=2)
    v.rep("step3.html", 'id="budget" name="budget"', 'id="spend-limit" name="budget"')
    v.rep("step3.html", '<th scope="row">Pro</th>', '<th scope="row">Growth</th>')
    v.rep("step3.html", '<th scope="row">Team</th>', '<th scope="row">Business</th>')
    v.rep("step3.html", 'aria-pressed="false">Select</button>', 'aria-pressed="false">Choose</button>', count=3)
    v.rep("step3.html", 'aria-label="Decrease seats"', 'aria-label="Remove a seat"')
    v.rep("step3.html", 'aria-label="Increase seats"', 'aria-label="Add a seat"')
    v.rep("step3.html", '<summary>Advanced options</summary>', '<summary>More options</summary>')
    v.rep("step3.html", '<label for="referral">Referral code</label>', '<label for="referral">Invite code</label>')
    v.rep("step3.html", '<label for="cardname">Name on card</label>', '<label for="cardname">Cardholder</label>')
    v.rep("step3.html", '<label for="cardnumber">Card number</label>', '<label for="cardnumber">Number on card</label>')
    v.rep("step3.html", 'id="cvc" name="cvc"', 'id="cvc" name="security_code"')
    v.rep("step3.html", 'id="next">Next</button>', 'id="continue">Continue</button>')
    # review
    v.rep("review.html", '>Read terms</button>', '>View terms</button>')
    v.rep("review.html", 'I agree to the terms</label>', 'I accept the terms and conditions</label>')
    v.rep("review.html", '>Close</button>', '>Dismiss</button>')
    v.rep("review.html", '>Submit order</button>', '>Place order</button>')

    C = [
        ('step 1 label "Full name" -> "Name and surname"',
         [('get_by_label("Full name")', 'getByLabel("Name and surname"); same field, same position')]),
        ('step 1 label "Email" -> "Email address", placeholder "you@example.com" -> "name@company.com"',
         [('get_by_placeholder("you@example.com")', 'getByPlaceholder("name@company.com") or getByLabel("Email address")')]),
        ('step 1 label "Username" -> "Login handle", data-testid "username-input" -> "account-username"',
         [('get_by_test_id("username-input")', 'getByTestId("account-username")')]),
        ('step 1 password id "password" -> "pwd" (name unchanged)',
         [('locator("#password")', 'locator("#pwd") or locator("input[name=password]")')]),
        ('step 1 confirm: label "Confirm password" -> "Repeat password", id "confirm" -> "pwd2", name "confirm" -> "password_confirm"',
         [('locator("input[name=confirm]")', 'locator("input[name=password_confirm]") or getByLabel("Repeat password")')]),
        ('step 1 button "Next" -> "Continue" (id next -> continue)',
         [('get_by_role("button", name="Next")', 'getByRole("button", name="Continue")')]),
        ('step 2 label "Phone" -> "Mobile number"',
         [('get_by_label("Phone")', 'getByLabel("Mobile number")')]),
        ('step 2 label "Age" -> "Years old"',
         [('get_by_role("spinbutton", name="Age")', 'getByRole("spinbutton", name="Years old")')]),
        ('step 2 label "Birth date" -> "Date of birth"',
         [('get_by_label("Birth date")', 'getByLabel("Date of birth")')]),
        ('step 2 date picker button "Choose delivery date" -> "Pick a delivery day"',
         [('get_by_role("button", name="Choose delivery date")', 'name="Pick a delivery day"')]),
        ('step 2 calendar data-testid "calendar" -> "date-popup"',
         [('get_by_test_id("calendar")', 'getByTestId("date-popup") (keep the scoped day button)')]),
        ('step 2 select name "country" -> "country_code"',
         [('locator("select[name=country]")', 'locator("select[name=country_code]") or getByLabel("Country")')]),
        ('step 2 label "City" -> "Town"',
         [('get_by_role("combobox", name="City")', 'getByRole("combobox", name="Town")')]),
        ('step 2 radio "Express" -> "Fast shipping"',
         [('get_by_role("radio", name="Express")', 'getByRole("radio", name="Fast shipping")')]),
        ('step 2 notes placeholder -> "Anything the courier should know"',
         [('get_by_placeholder("Gate code, drop-off instructions")', 'getByPlaceholder("Anything the courier should know") or getByLabel("Delivery notes")')]),
        ('step 2 button "Next" -> "Continue"',
         [('get_by_text("Next", exact=True)', 'getByText("Continue", exact=True)')]),
        ('step 3 tab "Billing contact" -> "Invoice contact", its field "Billing email" -> "Invoice email"',
         [('get_by_role("tab", name="Billing contact")', 'name="Invoice contact"'),
          ('get_by_label("Billing email")', 'getByLabel("Invoice email")')]),
        ('step 3 switch "Weekly digest" -> "Weekly summary"',
         [('get_by_role("switch", name="Weekly digest")', 'name="Weekly summary"')]),
        ('step 3 checkbox labels "Music" -> "Songs", "Travel" -> "Trips", "Marketing emails" -> "Promotional email"',
         [('get_by_label("Music")', 'getByLabel("Songs")'),
          ('get_by_label("Travel")', 'getByLabel("Trips")'),
          ('get_by_role("checkbox", name="Marketing emails")', 'name="Promotional email"')]),
        ('step 3 slider id "budget" -> "spend-limit" (label "Monthly budget" unchanged)',
         [('locator("#budget")', 'locator("#spend-limit") or getByLabel("Monthly budget")')]),
        ('step 3 plan rows "Pro" -> "Growth", "Team" -> "Business"; row buttons "Select" -> "Choose"',
         [('get_by_role("row", name="Pro")', 'name="Growth"; inner button name="Choose"'),
          ('get_by_role("row", name="Team")', 'name="Business"; inner button name="Choose"'),
          ('get_by_role("button", name="Select")', 'name="Choose" (the .nth(1) form keeps its index)')]),
        ('step 3 stepper buttons "Increase seats" -> "Add a seat", "Decrease seats" -> "Remove a seat"',
         [('get_by_role("button", name="Increase seats")', 'name="Add a seat"'),
          ('get_by_role("button", name="Decrease seats")', 'name="Remove a seat"')]),
        ('step 3 accordion summary "Advanced options" -> "More options", field "Referral code" -> "Invite code"',
         [('get_by_text("Advanced options")', 'getByText("More options")'),
          ('get_by_label("Referral code")', 'getByLabel("Invite code")')]),
        ('step 3 payment labels "Name on card" -> "Cardholder", "Card number" -> "Number on card"; cvc name "cvc" -> "security_code"; button id next -> continue',
         [('get_by_label("Name on card")', 'getByLabel("Cardholder")'),
          ('get_by_label("Card number")', 'getByLabel("Number on card")'),
          ('locator("input[name=cvc]")', 'locator("input[name=security_code]")'),
          ('locator("#next")', 'locator("#continue") or getByRole("button", name="Continue")')]),
        ('review buttons "Read terms" -> "View terms", "Close" -> "Dismiss", "Submit order" -> "Place order"; modal checkbox "I agree to the terms" -> "I accept the terms and conditions"',
         [('get_by_role("button", name="Read terms")', 'name="View terms"'),
          ('get_by_label("I agree to the terms")', 'getByLabel("I accept the terms and conditions")'),
          ('get_by_role("button", name="Close")', 'name="Dismiss"'),
          ('get_by_role("button", name="Submit order")', 'name="Place order"')]),
        ('unchanged: layout, flow, field order, the done page data-testids (end-result assertions) and the live-summary box', []),
    ]
    write(v.path("CHANGES.md"), changes_md("v1 - renames (pure locator drift)",
        "Same layout and flow as base. Every change is a rename of a label, id, test id, placeholder, "
        "button text or tab name. Every broken line is a drift: the element is still where it was, "
        "so the correct heal is a new locator for the same element and the same action.", C))


# ---------------------------------------------------------------- v6: the same site in French
FR = {
    "Step 1 of 4": "Étape 1 sur 4", "Step 2 of 4": "Étape 2 sur 4", "Step 3 of 4": "Étape 3 sur 4", "Step 4 of 4": "Étape 4 sur 4",
    "Sign up - Account": "Inscription - Compte", "Sign up - Profile and shipping": "Inscription - Profil et livraison",
    "Sign up - Preferences and payment": "Inscription - Préférences et paiement", "Sign up - Review": "Inscription - Vérification",
    "Sign up - Done": "Inscription - Terminé",
    "Create your account": "Créez votre compte", "Full name": "Nom et prénom", "Email": "Adresse e-mail",
    "Username": "Identifiant", "Password": "Mot de passe", "At least 8 characters.": "Au moins 8 caractères.",
    "Confirm password": "Confirmez le mot de passe", "Next": "Suivant", "you@example.com": "vous@exemple.fr",
    "Profile and shipping": "Profil et livraison", "Phone": "Téléphone", "Age": "Âge", "Birth date": "Date de naissance",
    "Choose delivery date": "Choisir la date de livraison", "No date chosen": "Aucune date choisie",
    "Delivery date": "Date de livraison", "Country": "Pays", "Select a country": "Choisissez un pays",
    "United States": "États-Unis", "Canada": "Canada", "United Kingdom": "Royaume-Uni", "Germany": "Allemagne",
    "City": "Ville", "Suggested cities": "Villes suggérées", "Shipping method": "Mode de livraison",
    "Standard": "Standard", "Express": "Express", "Overnight": "Livraison le lendemain",
    "Delivery notes": "Instructions de livraison", "Gate code, drop-off instructions": "Code d'accès, consignes de dépôt",
    "Digits, spaces and + ( ) - only": "Chiffres, espaces et + ( ) - uniquement",
    "Preferences and payment": "Préférences et paiement", "Preference groups": "Groupes de préférences",
    "Notifications": "Notifications", "Weekly digest": "Résumé hebdomadaire", "Privacy": "Confidentialité",
    "Show my profile publicly": "Rendre mon profil public", "Billing contact": "Contact de facturation",
    "Billing email": "E-mail de facturation", "Interests": "Centres d'intérêt", "Sports": "Sport", "Music": "Musique",
    "Travel": "Voyages", "Marketing emails": "E-mails marketing", "Monthly budget": "Budget mensuel", "Plan": "Formule",
    "Sites": "Sites", "Support": "Assistance", "Price": "Prix", "Action": "Action", "Basic": "Essentiel",
    "Pro": "Pro", "Team": "Équipe", "Up to 3 sites": "Jusqu'à 3 sites", "Unlimited sites": "Sites illimités",
    "Community": "Communauté", "Priority": "Prioritaire", "Dedicated manager": "Responsable dédié", "Select": "Choisir",
    "Seats": "Places", "Increase seats": "Ajouter une place", "Decrease seats": "Retirer une place",
    "Advanced options": "Options avancées", "Referral code": "Code de parrainage", "Payment": "Paiement",
    "Name on card": "Nom sur la carte", "Card number": "Numéro de carte", "CVC": "Cryptogramme",
    "16 digits": "16 chiffres", "3 or 4 digits": "3 ou 4 chiffres",
    "Review and submit": "Vérifier et envoyer", "Terms and conditions": "Conditions générales", "Read terms": "Lire les conditions",
    "Terms not accepted yet": "Conditions pas encore acceptées", "Terms of service": "Conditions d'utilisation",
    "You may cancel at any time. No refunds are issued for the unused part of a billing period.":
        "Vous pouvez résilier à tout moment. La période de facturation entamée n'est pas remboursée.",
    "I agree to the terms": "J'accepte les conditions", "Close": "Fermer", "Submit order": "Valider la commande",
    "By submitting this order you confirm that the details above are correct and that you\n          are allowed to use the payment method provided. This is a test fixture: nothing is sent anywhere.":
        "En validant cette commande, vous confirmez que les informations ci-dessus sont exactes et que vous\n          êtes autorisé à utiliser ce moyen de paiement. Ceci est un jeu de test : rien n'est envoyé.",
    "Done": "Terminé", "Order confirmed": "Commande confirmée",
    "Thanks for signing up. Here is what we received.": "Merci pour votre inscription. Voici ce que nous avons reçu.",
    "Public profile": "Profil public", "Terms accepted": "Conditions acceptées",
}
FR_JS = [("' is required'", "' est obligatoire'"), ("'Passwords do not match'", "'Les mots de passe ne correspondent pas'"),
         ("'Terms accepted' : 'Terms not accepted yet'", "'Conditions acceptées' : 'Conditions pas encore acceptées'"),
         ("['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']",
          "['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre']")]


def _tr_text(m):
    raw = m.group(1)
    key = raw.strip()
    if key not in FR:
        return m.group(0)
    lead, tail = raw[:len(raw) - len(raw.lstrip())], raw[len(raw.rstrip()):]
    return ">" + lead + FR[key] + tail + "<"


def _tr_attr(m):
    return f'{m.group(1)}="{FR.get(m.group(2), m.group(2))}"'


def build_v6():
    v = Variant("v6")
    for f in sorted(os.listdir(v.dir)):
        if f.endswith(".html"):
            s = read(v.path(f)).replace('<html lang="en">', '<html lang="fr">')
            s = re.sub(r">([^<>]+)<", _tr_text, s)
            s = re.sub(r'\b(placeholder|aria-label|title|alt)="([^"]*)"', _tr_attr, s)
            write(v.path(f), s)
    for old, new in FR_JS:
        v.rep("app.js", old, new)
    C = [
        ("Every visible string is French (labels, buttons, headings, placeholders, aria-labels, titles, validation messages, "
         "month names). Every id, name, data-testid, option value and data-key is unchanged, so the summaries the "
         "assertions read are unchanged.",
         [('get_by_label("Full name")', 'COPY-BASED. The element still has id="fullname" / name="fullname": '
           'the correct heal is `page.locator("#fullname")`, which passes in English AND French. Rewriting it to '
           '`get_by_label("Nom et prénom")` would pass here and break the English site.'),
          ('get_by_role("button", name="Next")', 'COPY-BASED. The button has id="next": `page.locator("#next")`.')]),
        ("Lines that already use a test id, an id, a name or an option value pass unchanged.", []),
    ]
    write(v.path("CHANGES.md"), changes_md("v6 - French",
        "The base site translated to French and nothing else. It separates locators that depend on the copy from "
        "locators that depend on the markup: only the first kind breaks, and the right heal moves them to the second kind.", C))


# ================================================================ orders spec: v7 .. v9 from site/orders-base
# Locators of tests/python/test_orders.py (py syntax; the ts spec uses the same calls camelCased).
L_STATUS_BTN = 'page.get_by_test_id("orders-table").locator("tbody tr").nth(3).locator("td").nth(2).locator("button")'
L_XPATH = 'page.locator("//table//tr[3]/td[2]")'
L_GRID = 'page.locator("#app > div > div:nth-child(2) > div.grid > div:nth-child(3) > span.cell")'
L_MENU_BTN = 'page.get_by_label("Actions for order 1043")'
L_ROW_CHECK = 'page.get_by_role("row").filter(has_text="Echo GmbH").get_by_role("checkbox")'
L_CHIP = 'page.locator("div.toolbar div.filters > button:nth-of-type(2)")'
L_STATUS_Q = 'page.locator("[data-qa=\'order-row-1042\'] [data-qa=\'status\']")'
L_EXPORT = 'page.locator("[data-cy=export-csv]")'
L_TAB = 'page.locator("#tab-history")'
L_LI = 'page.locator("[role=tabpanel][aria-labelledby=tab-history] li").first'
L_NOTE = 'page.locator("#panel-notes > textarea")'
L_CANCEL = 'page.locator("button.sc-fzXfKy.jQdBvX")'
L_CLOSE = 'page.locator("[aria-label=\'Close dialog\']")'
L_ZIP = 'page.locator("input[name=zip]")'
L_FRAME = 'page.frame_locator("#payment-frame")'
L_TOGGLE = 'page.locator("order-flag button.toggle")'

ORDERS_GOLDEN = {}   # variant -> [{"old": py expression, "new": py expression or null (= delete the line)}]


def cm(name):
    """A CSS-module style hashed class name (idempotent)."""
    return name if name.startswith("_") else "_%s_%s" % (name, hashlib.md5(name.encode()).hexdigest()[:5])


def cssmodule(v):
    """Rename every class of the orders site to a hashed CSS-module name (html, css and the classes the script writes)."""
    v.rep("index.html", 'class="sc-fzXfKy jQdBvX"', 'class="%s"' % cm("cancel"))
    v.rep("style.css", ".sc-fzXfKy.jQdBvX", "." + cm("cancel"))
    for f in ("index.html", "payment.html"):
        s = re.sub(r'class="([^"]*)"', lambda m: 'class="%s"' % " ".join(cm(c) for c in m.group(1).split()), read(v.path(f)))
        write(v.path(f), s)
    write(v.path("style.css"), re.sub(r"\.([A-Za-z_][\w-]*)", lambda m: "." + cm(m.group(1)), read(v.path("style.css"))))
    v.rep("app.js", '<div class="menu" role="menu"', '<div class="%s" role="menu"' % cm("menu"))
    v.rep("app.js", "sub.className = 'menu submenu';", "sub.className = '%s %s';" % (cm("menu"), cm("submenu")))
    v.rep("app.js", 'class="toggle" role="switch"', 'class="%s" role="switch"' % cm("toggle"))
    v.rep("app.js", "root.querySelector('.toggle')", "root.querySelector('.%s')" % cm("toggle"))


def swap_children(v, f, first, second, end):
    """Swap two sibling blocks of f: text from `first` to `second`, and from `second` to `end`."""
    s = read(v.path(f))
    i, j, k = s.index(first), s.index(second), s.index(end)
    assert i < j < k and s.count(first) == 1 and s.count(second) == 1, f"{v.name}/{f}: blocks not found once and in order"
    write(v.path(f), s[:i] + s[j:k] + s[i:j] + s[k:])


def orders_changes_md(v, title, intro, changes):
    out = [f"# {title}", "", intro, "",
           "Test lines are looked up by locator text; `py` = tests/python/test_orders.py, `ts` = tests/ts/orders.spec.ts "
           "(same calls, camelCased). A line that is not listed still passes. Heals are written in the python syntax and "
           "follow the locator policy: test id > hand-written id > name > css on a non-copy attribute > role+name > label > "
           "text > structural chain. `verify_golden.py` applies every heal listed here and runs the suite, so each one is "
           "known to pass.", ""]
    for ch, breaks in changes:
        out.append(f"- {ch}")
        if not breaks:
            out.append("  - no test line is affected (nothing to heal).")
        for old, why, new in breaks:
            note = f"`{new}`" if new else "NONE: the element is gone, delete the step (no substitute)"
            out.append(f"  - affects `{old}` ({where(old, OTL)}): {why}. Heal: {note}")
            ORDERS_GOLDEN.setdefault(v.name, []).append({"old": old, "new": new})
    out.append("")
    write(v.path("CHANGES.md"), "\n".join(out))


# ---------------------------------------------------------------- v7: restructure
def build_v7():
    v = Variant("v7", "orders-base")
    # wrappers inserted, a banner added, main and side column swapped in the DOM (CSS keeps them where they were)
    v.rep("index.html", '<div id="app" data-state="idle">\n  <div class="shell">',
          '<div id="app" data-state="idle">\n  <div class="layout-root">\n'
          '  <div class="notice-bar" role="status">Scheduled maintenance tonight at 02:00 UTC</div>\n  <div class="shell">')
    v.rep("index.html", '  </div>\n</div>\n<div id="portal-root"></div>', '  </div>\n  </div>\n</div>\n<div id="portal-root"></div>')
    swap_children(v, "index.html", '    <div class="main-col">', '    <div class="side-col">',
                  '  </div>\n  </div>\n</div>\n<div id="portal-root"></div>')
    v.rep("style.css", ".shell { display: flex;", ".shell { flex-direction: row-reverse; display: flex;")
    v.rep("style.css", "/* table */", ".layout-root { max-width: 1120px; margin: 0 auto; }\n"
          ".layout-root > .shell { max-width: none; }\n"
          ".notice-bar { background: #fff4d6; border: 1px solid #ecd48a; border-radius: var(--radius); padding: 6px 12px; margin-bottom: 12px; font-size: 12px; }\n"
          "table.orders tr.divider th { background: #f4f5f7; font-size: 12px; color: var(--muted); }\n\n/* table */")
    # table: Total before Status in every row, and a divider row between two groups of orders
    s = read(v.path("index.html"))
    s, n = re.subn(r'(                  <td data-col="status">.*?</td>\n)(                  <td data-col="total".*?</td>\n)', r"\2\1", s)
    assert n == 6, n
    write(v.path("index.html"), s)
    v.rep("index.html", '<th scope="col">Status</th>\n                  <th scope="col">Total</th>',
          '<th scope="col">Total</th>\n                  <th scope="col">Status</th>')
    v.rep("index.html", '                <tr data-qa="order-row-1042">',
          '                <tr class="divider"><th scope="rowgroup" colspan="5">Earlier orders</th></tr>\n                <tr data-qa="order-row-1042">')
    # filter chips: Shipped now comes before Paid
    swap_children(v, "index.html", '          <button type="button" class="chip" data-filter="paid"',
                  '          <button type="button" class="chip" data-filter="shipped"',
                  '          <button type="button" class="chip" data-filter="refunded"')
    # the note textarea gets two wrapper divs
    v.rep("index.html", '            <textarea name="note" rows="3" placeholder="Internal note"></textarea>',
          '            <div class="field-wrap"><div class="field-wrap-inner">\n'
          '              <textarea name="note" rows="3" placeholder="Internal note"></textarea>\n            </div></div>')
    cssmodule(v)
    C = [
        ("A banner and a `layout-root` wrapper are inserted around the shell, and the side column now comes BEFORE the main "
         "column in the DOM (a flex `row-reverse` keeps them where they were on screen).",
         [(L_GRID, "`#app > div` is now the wrapper, its second child is the shell, and `.grid` is not a direct child of "
           "the second column; the classes are hashed too. The grid rows carry `data-activity` and grid row 3 is order 1041",
           'page.locator("[data-activity=\'1041\']")')]),
        ("Table: the Total column now comes before Status in every row (header too), and a divider row `Earlier orders` "
         "sits between order 1041 and 1042. Row order and the order ids are unchanged.",
         [(L_STATUS_BTN, "the 4th body row is now order 1042 (the divider is row 3) and td 2 is Total, with no button: times "
           "out. The step wants order 1043's status button, so a positional edit (td 3) would click 1042's button and fail "
           "the `status.1043` assertion",
           'page.locator("[data-qa=\'order-row-1043\'] [data-qa=\'status\']")'),
          (L_XPATH, "tr[3] is now the divider row, which has no td: times out. The step wants order 1042's customer cell "
           "(tr[4] would pass but is the weakest heal)",
           'page.locator("[data-qa=\'order-row-1042\'] [data-col=customer]")')]),
        ("Filter chips reordered: All, Shipped, Paid, Refunded. Every class name is renamed to a hashed CSS-module name "
         "(`toolbar` -> `_toolbar_xxxxx`, `filters`, `chip`, ...).",
         [(L_CHIP, "the classes are gone and `button:nth-of-type(2)` would now be Shipped. A new structural chain would "
           "resolve but click the wrong chip and fail `filter: paid`; the chip carries `data-filter`",
           'page.locator("[data-filter=paid]")')]),
        ("The notes textarea is wrapped in two divs (`field-wrap`, `field-wrap-inner`).",
         [(L_NOTE, "`#panel-notes > textarea` has no direct child any more; the textarea keeps its `name`",
           'page.locator("textarea[name=note]")')]),
        ("The Cancel order button's generated class `sc-fzXfKy jQdBvX` becomes `_cancel_xxxxx`. It has no id, test id, data "
         "attribute or aria-label: only its text.",
         [(L_CANCEL, "nothing stable is left but role + name", 'page.get_by_role("button", name="Cancel order")')]),
        ("The class `toggle` inside the `<order-flag>` shadow root is hashed.",
         [(L_TOGGLE, "the class is gone; the button is the only `role=switch` in the component",
           'page.locator("order-flag [role=switch]")')]),
        ("Everything addressed by id, data-qa, data-cy, data-testid, aria-label, aria-labelledby, role or name is unchanged.", []),
    ]
    orders_changes_md(v, "v7 - restructure",
        "Wrappers inserted, sibling order changed, table columns reordered, classes renamed to hashed CSS-module names. "
        "The structural, nth and class chains break; data-*, aria and ids stay, so the right heals move to those. "
        "Row text, order ids and statuses are unchanged, so no assertion changes.", C)


# ---------------------------------------------------------------- v8: attribute churn
def build_v8():
    v = Variant("v8", "orders-base")
    v.rep("index.html", 'data-qa="order-row-', 'data-testid="order-row-', 6)
    v.rep("index.html", 'data-qa="status"', 'data-testid="status"', 6)
    v.rep("index.html", 'data-cy="export-csv"', 'data-testid="export-csv"')
    v.rep("index.html", 'data-cy="export-pdf"', 'data-testid="export-pdf"')
    v.rep("index.html", 'aria-label="Actions for order ', 'aria-label="Open menu for order ', 6)
    v.rep("index.html", 'aria-label="Close dialog"', 'aria-label="Dismiss"')
    gen = {"tab-details": ":r1:", "tab-history": ":r2:", "tab-notes": ":r3:",
           "panel-details": ":r4:", "panel-history": ":r5:", "panel-notes": ":r6:"}
    for old, new in gen.items():
        v.rep("index.html", f' id="{old}"', f' id="{new}"')
        ref = "aria-labelledby" if old.startswith("tab-") else "aria-controls"
        v.rep("index.html", f'{ref}="{old}"', f'{ref}="{new}"')
    v.rep("index.html", ' id="payment-frame"', ' id=":r7:"')
    v.rep("style.css", "#payment-frame {", "iframe[name=payment] {")
    v.rep("index.html", '<label for="zip">', '<label for=":r8:">')
    v.rep("index.html", ' id="zip"', ' id=":r8:"')
    v.rep("index.html", '<label for="reason">', '<label for=":r9:">')
    v.rep("index.html", ' id="reason"', ' id=":r9:"')
    C = [
        ("`data-qa` -> `data-testid` on the order rows (`order-row-1040` ...) and on the status badges (`status`).",
         [(L_STATUS_Q, "both attributes are renamed, values unchanged",
           'page.get_by_test_id("order-row-1042").get_by_test_id("status")')]),
        ("`data-cy=export-csv` / `export-pdf` -> `data-testid`.",
         [(L_EXPORT, "same value under the test id attribute", 'page.get_by_test_id("export-csv")')]),
        ("Row menu button aria-label `Actions for order N` -> `Open menu for order N`; dialog close `Close dialog` -> `Dismiss`. "
         "Both buttons keep a data attribute (`data-cy=row-menu`, `data-qa=dialog-close`).",
         [(L_MENU_BTN, "label reworded. Heal through the stable attribute, not to the new label (a label-to-label heal is wrong)",
           'page.get_by_test_id("order-row-1043").locator("[data-cy=row-menu]")'),
          (L_CLOSE, "label reworded to `Dismiss`; `data-qa=dialog-close` is stable",
           'page.locator("[data-qa=dialog-close]")')]),
        ("Hand-written ids replaced by generated ids (`:r1:` style) on the three tabs, the three panels, the payment iframe, "
         "the ZIP input and the reason select; `aria-labelledby` / `aria-controls` / `for` follow. The tabs keep "
         "`data-testid=tab-*`, the panels keep `data-panel`, the iframe keeps `name=payment`, the textarea keeps `name=note`.",
         [(L_TAB, "id is now `:r2:` (generated, never use it); the tab has a test id", 'page.get_by_test_id("tab-history")'),
          (L_LI, "`aria-labelledby` now points at the generated id; the panel has `data-panel=history`",
           'page.locator("[role=tabpanel][data-panel=history] li").first'),
          (L_NOTE, "id is now generated; the textarea has `name=note`", 'page.locator("textarea[name=note]")'),
          (L_FRAME, "`#payment-frame` is gone (three lines); the iframe has `name=payment`",
           'page.frame_locator("iframe[name=payment]")')]),
        ("The DOM structure, classes, copy and the table are unchanged; everything else (including `[data-cy=row-menu]`) passes.", []),
    ]
    orders_changes_md(v, "v8 - attribute churn",
        "Same structure as base. Test-hook attributes are renamed, aria-labels reworded and hand-written ids replaced by "
        "generated ones. The right heal is the attribute that is still stable on the same element; generated ids and "
        "the new copy are the traps.", C)


# ---------------------------------------------------------------- v9: mixed
def build_v9():
    v = Variant("v9", "orders-base")
    # structure: the filter chips get a wrapper div
    v.rep("index.html", 'aria-label="Status filter">\n          <button type="button" class="chip" data-filter="all"',
          'aria-label="Status filter">\n          <div class="chip-group">\n          <button type="button" class="chip" data-filter="all"')
    v.rep("index.html", 'data-filter="refunded" aria-pressed="false">Refunded</button>\n',
          'data-filter="refunded" aria-pressed="false">Refunded</button>\n          </div>\n')
    v.rep("style.css", "/* table */", ".chip-group { display: flex; gap: 6px; }\n\n/* table */")
    # generated class regenerated by the build
    v.rep("index.html", 'class="sc-fzXfKy jQdBvX"', 'class="sc-dkzDqf kLmNoP"')
    v.rep("style.css", ".sc-fzXfKy.jQdBvX", ".sc-dkzDqf.kLmNoP")
    # attributes: status hook value, tab id, close label
    v.rep("index.html", 'data-qa="status"', 'data-qa="order-status"', 6)
    v.rep("index.html", ' id="tab-history"', ' id=":r3:"')
    v.rep("index.html", 'aria-labelledby="tab-history"', 'aria-labelledby=":r3:"')
    v.rep("index.html", 'aria-label="Close dialog"', 'aria-label="Close"')
    # copy: a customer is renamed
    v.rep("index.html", "Echo GmbH", "Echo Group GmbH")
    # removed: Export CSV (Export PDF stays)
    v.rep("index.html", '          <button type="button" class="btn" data-cy="export-csv" data-format="csv">Export CSV</button>\n', "")
    # moved into the payment iframe: the billing ZIP
    v.rep("index.html", """            <div class="field">
              <label for="zip">Billing ZIP</label>
              <input type="text" id="zip" name="zip" autocomplete="off">
            </div>
""", "")
    v.rep("payment.html", '  <div class="actions"><button type="submit"', """  <div class="field">
    <label for="zip">Billing ZIP</label>
    <input type="text" id="zip" name="zip" autocomplete="off">
  </div>
  <div class="actions"><button type="submit\"""")
    v.rep("payment.html", "exp: form.elements.exp.value }", "exp: form.elements.exp.value, zip: form.elements.zip.value }")
    v.rep("style.css", "#payment-frame { width: 100%; height: 190px;", "#payment-frame { width: 100%; height: 260px;")
    # moved into a shadow root: the note textarea becomes a <note-editor> component
    v.rep("index.html", '            <textarea name="note" rows="3" placeholder="Internal note"></textarea>',
          '            <note-editor></note-editor>')
    v.rep("app.js", "function noteField() { return one('textarea[name=note]'); }",
          "function noteField() { return one('note-editor').shadowRoot.querySelector('textarea'); }")
    v.rep("app.js", "  /* ---------- boot ---------- */",
          """  class NoteEditor extends HTMLElement {
    connectedCallback() {
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = '<style>textarea{width:100%;min-height:70px;padding:7px 9px;border:1px solid #d9dde3;border-radius:6px;font:inherit;box-sizing:border-box}</style>' +
        '<textarea name="note" rows="3" placeholder="Internal note"></textarea>';
    }
  }
  customElements.define('note-editor', NoteEditor);

  /* ---------- boot ---------- */""")
    C = [
        ("Structure: the filter chips are wrapped in a `div.chip-group`.",
         [(L_CHIP, "`div.filters > button` has no direct button children any more; the classes are unchanged. The chip has `data-filter`",
           'page.locator("[data-filter=paid]")')]),
        ("Class: the generated class of the Cancel order button changes (`sc-fzXfKy jQdBvX` -> `sc-dkzDqf kLmNoP`).",
         [(L_CANCEL, "the build regenerated the class; nothing stable but role + name", 'page.get_by_role("button", name="Cancel order")')]),
        ("Attribute: the status hook value `data-qa=status` -> `data-qa=order-status` (rows keep `data-qa=order-row-N`); "
         "the history tab id becomes generated (`:r3:`, `data-testid=tab-history` stays) and so does the panel's aria-labelledby; "
         "the close button aria-label `Close dialog` -> `Close` (`data-qa=dialog-close` stays).",
         [(L_STATUS_Q, "the value moved to `order-status`; the row hook is unchanged",
           'page.locator("[data-qa=\'order-row-1042\'] [data-qa=\'order-status\']")'),
          (L_TAB, "generated id, never use it", 'page.get_by_test_id("tab-history")'),
          (L_LI, "`aria-labelledby` is generated now; `data-panel=history` is stable",
           'page.locator("[role=tabpanel][data-panel=history] li").first'),
          (L_CLOSE, "label reworded; heal through `data-qa=dialog-close`, not to `Close`",
           'page.locator("[data-qa=dialog-close]")')]),
        ("Copy: customer `Echo GmbH` -> `Echo Group GmbH`.",
         [(L_ROW_CHECK, "`has_text=\"Echo GmbH\"` matches no row. The row has `data-qa=order-row-1044`",
           'page.locator("[data-qa=\'order-row-1044\']").get_by_role("checkbox")')]),
        ("Removed: the Export CSV button is gone (Export PDF is still there; it is NOT a substitute).",
         [(L_EXPORT, "the element no longer exists; the right answer is NONE, never `export-pdf`", None)]),
        ("Moved into the payment iframe: the Billing ZIP input leaves the page and sits in `payment.html` (the iframe) with the "
         "same `name=zip`. The iframe grows taller.",
         [(L_ZIP, "the input is now inside `#payment-frame`", 'page.frame_locator("#payment-frame").locator("input[name=zip]")')]),
        ("Moved into a shadow root: the notes textarea becomes a `<note-editor>` custom element with an open shadow root "
         "(same `name=note`).",
         [(L_NOTE, "`#panel-notes > textarea` has no direct child. Playwright CSS pierces the open shadow root, so "
           "`textarea[name=note]` works as is", 'page.locator("textarea[name=note]")')]),
        ("Unchanged: the table, columns, row order, the activity grid, the menu, the dialog, the payment frame id, the flag component.", []),
    ]
    orders_changes_md(v, "v9 - mixed",
        "A few changes of every kind: a wrapper, a regenerated class, attribute and copy changes, one element removed "
        "(expect NONE), one moved into the iframe and one into a shadow root. Several lines must NOT be touched.", C)

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    assert which in ("signup", "orders", "all"), "usage: build_variants.py [signup|orders|all]"
    fns = []
    if which in ("signup", "all"):
        fns += [build_v1, build_v6]
    if which in ("orders", "all"):
        fns += [build_v7, build_v8, build_v9]
    for fn in fns:
        fn()
        print("built", fn.__name__[6:])
    if which in ("orders", "all"):
        write(GOLDEN, json.dumps(ORDERS_GOLDEN, indent=2, ensure_ascii=False) + "\n")
        print("wrote", os.path.basename(GOLDEN))
