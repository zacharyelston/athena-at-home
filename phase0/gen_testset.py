# SPDX-License-Identifier: GPL-3.0-or-later
"""Render synthetic documents with exact ground truth: the household pages of the Phase 0 OCR benchmark, and the
historical styles (mimeograph, typewriter, newsprint, a program with a photograph, handwriting) for the capture pilot.

Every name, number and address here is made up. Pages are drawn at 300 DPI (US Letter), then resampled to the
benchmark DPIs in two conditions:
  clean   resampled only (a flatbed-like scan)
  camera  mild blur, uneven light, sensor noise and JPEG, roughly what a deskewed camera capture looks like

Output: testset/<doc>/{gt.txt, fields.json, <cond>_<dpi>.png|jpg} (or $TESTSET_OUT/<doc>/…)
"""
import json
import os
import random

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

MASTER_DPI = 300
W, H = int(8.5 * MASTER_DPI), int(11 * MASTER_DPI)
DPIS = [100, 150, 200]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testset")

# Each style lists (path, ttc index) candidates: macOS system fonts first, then metric-compatible Linux fonts
# (fonts-liberation, fonts-dejavu-core), so the fixtures render in CI. Ground truth doesn't depend on the font.
_MAC = "/System/Library/Fonts"
_LIB = "/usr/share/fonts/truetype/liberation"
_DJV = "/usr/share/fonts/truetype/dejavu"
FONTS = {
    ("sans", False): [(f"{_MAC}/Helvetica.ttc", 0), (f"{_LIB}/LiberationSans-Regular.ttf", 0), (f"{_DJV}/DejaVuSans.ttf", 0)],
    ("sans", True): [(f"{_MAC}/Helvetica.ttc", 1), (f"{_LIB}/LiberationSans-Bold.ttf", 0), (f"{_DJV}/DejaVuSans-Bold.ttf", 0)],
    ("arial", False): [(f"{_MAC}/Supplemental/Arial.ttf", 0), (f"{_LIB}/LiberationSans-Regular.ttf", 0),
                       (f"{_DJV}/DejaVuSans.ttf", 0)],
    ("arial", True): [(f"{_MAC}/Supplemental/Arial Bold.ttf", 0), (f"{_LIB}/LiberationSans-Bold.ttf", 0),
                      (f"{_DJV}/DejaVuSans-Bold.ttf", 0)],
    ("serif", False): [(f"{_MAC}/Supplemental/Times New Roman.ttf", 0), (f"{_LIB}/LiberationSerif-Regular.ttf", 0),
                       (f"{_DJV}/DejaVuSerif.ttf", 0)],
    ("mono", False): [(f"{_MAC}/Supplemental/Courier New.ttf", 0), (f"{_LIB}/LiberationMono-Regular.ttf", 0),
                      (f"{_DJV}/DejaVuSansMono.ttf", 0)],
    ("georgia", False): [(f"{_MAC}/Supplemental/Georgia.ttf", 0), (f"{_LIB}/LiberationSerif-Regular.ttf", 0),
                         (f"{_DJV}/DejaVuSerif.ttf", 0)],
    ("georgia", True): [(f"{_MAC}/Supplemental/Georgia Bold.ttf", 0), (f"{_LIB}/LiberationSerif-Bold.ttf", 0),
                        (f"{_DJV}/DejaVuSerif-Bold.ttf", 0)],
    ("serif", True): [(f"{_MAC}/Supplemental/Times New Roman Bold.ttf", 0), (f"{_LIB}/LiberationSerif-Bold.ttf", 0),
                      (f"{_DJV}/DejaVuSerif-Bold.ttf", 0)],
    ("italic", False): [(f"{_MAC}/Supplemental/Times New Roman Italic.ttf", 0), (f"{_LIB}/LiberationSerif-Italic.ttf", 0),
                        (f"{_DJV}/DejaVuSerif.ttf", 0)],
    ("mono", True): [(f"{_MAC}/Supplemental/Courier New Bold.ttf", 0), (f"{_LIB}/LiberationMono-Bold.ttf", 0),
                     (f"{_DJV}/DejaVuSansMono-Bold.ttf", 0)],
    # A cursive hand. No Linux font is one, so CI falls back to an italic: the ground truth is the same, and the
    # tests check the scorer, not how well any engine reads handwriting.
    ("script", False): [(f"{_MAC}/Supplemental/SnellRoundhand.ttc", 0), (f"{_MAC}/Supplemental/Bradley Hand Bold.ttf", 0),
                        (f"{_LIB}/LiberationSerif-Italic.ttf", 0), (f"{_DJV}/DejaVuSerif.ttf", 0)],
}


def font_path(name, bold=False):
    """The first installed font for a style, as (path, ttc index). Bold falls back to regular if no bold exists."""
    for key in ((name, bold), (name, False)):
        for path, index in FONTS.get(key, []):
            if os.path.exists(path):
                return path, index
    raise FileNotFoundError(f"no font installed for {name!r} (bold={bold}); on Linux install fonts-liberation")


def font(name, pt, bold=False):
    path, index = font_path(name, bold)
    return ImageFont.truetype(path, int(pt * MASTER_DPI / 72), index=index)


class Page:
    """Tiny layout helper: draws text and records it, in reading order, as ground truth."""

    def __init__(self, margin_in=0.8):
        self.img = Image.new("L", (W, H), 255)
        self.d = ImageDraw.Draw(self.img)
        self.m = int(margin_in * MASTER_DPI)
        self.y = self.m
        self.lines = []
        self.fields = []

    def text(self, s, f, x=None, gap=0.35, record=True, field=False):
        x = self.m if x is None else x
        self.d.text((x, self.y), s, font=f, fill=0)
        if record:
            self.lines.append(s)
        if field:
            self.fields.append(s)
        self.y += int(f.size * (1 + gap))

    def row(self, cells, f, xs, aligns=None, gap=0.45, rule=False, fields=()):
        aligns = aligns or ["l"] * len(cells)
        for c, x, a in zip(cells, xs, aligns):
            tw = self.d.textlength(c, font=f)
            self.d.text((x - tw if a == "r" else x, self.y), c, font=f, fill=0)
        self.lines.append(" ".join(c for c in cells if c))
        for i in fields:
            self.fields.append(cells[i])
        self.y += int(f.size * (1 + gap))
        if rule:
            self.rule()

    def rule(self, w=3):
        self.d.line([(self.m, self.y - int(0.15 * MASTER_DPI) // 2), (W - self.m, self.y - int(0.15 * MASTER_DPI) // 2)],
                    fill=0, width=w)
        self.y += int(0.05 * MASTER_DPI)

    def space(self, inches):
        self.y += int(inches * MASTER_DPI)

    def paragraph(self, s, f, width_in=6.9, gap=0.3):
        words, line = s.split(), ""
        maxw = width_in * MASTER_DPI
        for w in words:
            t = (line + " " + w).strip()
            if self.d.textlength(t, font=f) > maxw:
                self.text(line, f, gap=gap)
                line = w
            else:
                line = t
        if line:
            self.text(line, f, gap=gap)


def money(v):
    return f"${v:,.2f}"


def bank_statement():
    p = Page()
    p.text("Riverbend Community Bank", font("sans", 20, True), gap=0.2)
    p.text("Member FDIC  |  PO Box 4100, Lakeview, ST 00012  |  1-800-555-0142", font("sans", 9))
    p.space(0.25)
    p.text("Alex and Jordan Sample", font("sans", 11))
    p.text("100 Example Lane", font("sans", 11))
    p.text("Springfield, ST 00000", font("sans", 11))
    p.space(0.25)
    p.text("Statement Period: September 1, 2026 - September 30, 2026", font("sans", 11, True), field=True)
    p.text("Everyday Checking  Account ending in 4821", font("sans", 11), field=True)
    p.space(0.15)
    xs = [p.m, int(4.8 * MASTER_DPI)]
    f = font("sans", 10.5)
    rng = random.Random(7)
    opening = 3214.56
    tx = [("09/02", "PAYROLL DEPOSIT ACME TOOLS", 2450.00), ("09/03", "SPRINGFIELD WATER UTIL", -64.18),
          ("09/05", "GROCER MART #212", -142.37), ("09/08", "CHECK 1043", -350.00),
          ("09/10", "ONLINE TRANSFER TO SAVINGS", -500.00), ("09/12", "PHARMACY PLUS", -23.49),
          ("09/15", "MORTGAGE PMT LAKEVIEW HOME LOANS", -1287.66), ("09/16", "PAYROLL DEPOSIT ACME TOOLS", 2450.00),
          ("09/19", "FUEL STOP 88", -48.20), ("09/22", "SCHOOL LUNCH ACCOUNT", -40.00),
          ("09/24", "ELECTRIC CO-OP AUTOPAY", -118.73), ("09/27", "GROCER MART #212", -96.81),
          ("09/29", "INTEREST PAID", 0.42), ("09/30", "MONTHLY SERVICE FEE WAIVED", 0.00)]
    deposits = sum(a for _, _, a in tx if a > 0)
    withdrawals = -sum(a for _, _, a in tx if a < 0)
    closing = opening + deposits - withdrawals
    p.row(["Beginning balance", money(opening)], f, [xs[0], W - p.m], ["l", "r"], fields=(1,))
    p.row(["Deposits and credits", money(deposits)], f, [xs[0], W - p.m], ["l", "r"], fields=(1,))
    p.row(["Withdrawals and debits", money(withdrawals)], f, [xs[0], W - p.m], ["l", "r"], fields=(1,))
    p.row(["Ending balance", money(closing)], font("sans", 10.5, True), [xs[0], W - p.m], ["l", "r"], fields=(1,))
    p.space(0.2)
    p.text("Transaction Detail", font("sans", 13, True), gap=0.25)
    cols = [p.m, int(1.75 * MASTER_DPI), int(6.0 * MASTER_DPI), W - p.m]
    p.row(["Date", "Description", "Amount", "Balance"], font("sans", 10, True), cols, ["l", "l", "r", "r"], rule=True)
    bal = opening
    for d, desc, a in tx:
        bal += a
        p.row([d, desc, f"{a:,.2f}", f"{bal:,.2f}"], font("sans", 10), cols, ["l", "l", "r", "r"], gap=0.55,
              fields=(2, 3))
    _ = rng
    return p


def utility_bill():
    p = Page()
    p.text("SPRINGFIELD ELECTRIC CO-OP", font("arial", 18, True), gap=0.2)
    p.text("Customer Service 1-800-555-0177  |  Outage line 1-800-555-0190", font("arial", 9))
    p.space(0.3)
    left = [("Account Number", "88-301-4471-02"), ("Service Address", "100 Example Lane, Springfield"),
            ("Billing Date", "10/03/2026"), ("Due Date", "10/24/2026"), ("Meter Number", "ZX-55190")]
    f, fb = font("arial", 11), font("arial", 11, True)
    for k, v in left:
        p.row([k, v], f, [p.m, int(3.2 * MASTER_DPI)], fields=(1,))
    p.space(0.25)
    p.text("Usage Summary", font("arial", 13, True), gap=0.2)
    cols = [p.m, int(3.6 * MASTER_DPI), int(5.4 * MASTER_DPI), W - p.m]
    p.row(["Period", "Previous", "Current", "kWh Used"], fb, cols, ["l", "r", "r", "r"], rule=True)
    p.row(["Sep 02 - Oct 01", "48,215", "49,107", "892"], f, cols, ["l", "r", "r", "r"], fields=(3,))
    p.space(0.25)
    p.text("Charges", font("arial", 13, True), gap=0.2)
    charges = [("Basic service charge", 22.00), ("Energy charge 892 kWh @ $0.1105", 98.57),
               ("Fuel cost adjustment", 6.42), ("State utility tax 4.5%", 5.73)]
    cols2 = [p.m, W - p.m]
    for k, v in charges:
        p.row([k, money(v)], f, cols2, ["l", "r"], fields=(1,))
    total = sum(v for _, v in charges)
    p.row(["Total Amount Due", money(total)], font("arial", 12, True), cols2, ["l", "r"], fields=(1,))
    p.space(0.35)
    p.paragraph("Payments received after the due date are subject to a late charge of 1.5% of the unpaid balance. "
                "To enroll in AutoPay or paperless billing, visit your online account or call Customer Service.",
                font("arial", 10))
    return p


def insurance_letter():
    p = Page(margin_in=1.0)
    p.text("Harborview Mutual Insurance", font("georgia", 17, True), gap=0.2)
    p.text("2200 Anchor Way, Bayside, ST 00031", font("georgia", 10))
    p.space(0.4)
    p.text("October 6, 2026", font("serif", 12), field=True)
    p.space(0.2)
    for l in ["Jordan Sample", "100 Example Lane", "Springfield, ST 00000"]:
        p.text(l, font("serif", 12), gap=0.2)
    p.space(0.25)
    p.text("Re: Policy HMI-2093-55817, Homeowners", font("serif", 12, True), field=True)
    p.space(0.15)
    p.text("Dear Jordan Sample,", font("serif", 12))
    p.space(0.1)
    body = [
        "Thank you for being a Harborview Mutual policyholder. This letter confirms the renewal of your homeowners "
        "policy for the term November 1, 2026 through November 1, 2027.",
        "Your renewal premium is $1,486.00, an increase of $64.00 from last year, mainly due to updated rebuilding "
        "cost estimates in your area. Your deductible remains $1,000 and your dwelling coverage is now $412,000.",
        "If you would like to review your coverage or discuss available discounts, such as those for monitored "
        "smoke detectors or bundling your auto policy, please contact your agent, Morgan Reyes, at 1-800-555-0163.",
    ]
    for para in body:
        p.paragraph(para, font("serif", 12), width_in=6.5)
        p.space(0.12)
    p.fields += ["$1,486.00", "$64.00", "$1,000", "$412,000", "1-800-555-0163"]
    p.space(0.2)
    p.text("Sincerely,", font("serif", 12))
    p.space(0.35)
    p.text("Policy Services Team", font("serif", 12))
    return p


def receipt():
    p = Page(margin_in=2.6)
    f, fb = font("mono", 11), font("mono", 11, True)
    right = W - p.m
    for l in ["GROCER MART #212", "45 Market Street", "Springfield ST 00000", "(555) 010-2212"]:
        tw = p.d.textlength(l, font=fb)
        p.d.text(((W - tw) / 2, p.y), l, font=fb, fill=0)
        p.lines.append(l)
        p.y += int(fb.size * 1.3)
    p.space(0.15)
    p.row(["10/11/2026 17:42", "TRANS 0381"], f, [p.m, right], ["l", "r"], fields=(0,))
    p.space(0.1)
    items = [("WHOLE MILK 1GAL", 4.29), ("EGGS LARGE 12CT", 3.89), ("BANANAS 2.31 LB", 1.36),
             ("BREAD WHEAT", 3.49), ("CHEDDAR 8OZ", 4.19), ("APPLES GALA 3LB", 5.99),
             ("COFFEE GROUND 12OZ", 8.99), ("PAPER TOWELS 6PK", 9.49), ("YOGURT 4PK", 4.79)]
    for k, v in items:
        p.row([k, f"{v:.2f}"], f, [p.m, right], ["l", "r"], gap=0.4, fields=(1,))
    sub = sum(v for _, v in items)
    tax = round(sub * 0.0, 2)
    p.space(0.1)
    p.row(["SUBTOTAL", f"{sub:.2f}"], f, [p.m, right], ["l", "r"], fields=(1,))
    p.row(["TAX", f"{tax:.2f}"], f, [p.m, right], ["l", "r"])
    p.row(["TOTAL", f"{sub + tax:.2f}"], fb, [p.m, right], ["l", "r"], fields=(1,))
    p.row(["VISA ****7730", f"{sub + tax:.2f}"], f, [p.m, right], ["l", "r"])
    p.space(0.2)
    p.row(["ITEMS SOLD", str(len(items))], f, [p.m, right], ["l", "r"])
    p.text("THANK YOU FOR SHOPPING", fb, x=p.m)
    return p


def intake_form():
    p = Page()
    p.text("Maple Grove Family Medicine", font("sans", 17, True), gap=0.2)
    p.text("New Patient Information Form", font("sans", 12), gap=0.4)
    f, fb = font("sans", 10), font("sans", 11)
    pairs = [("Patient name", "Riley Sample"), ("Date of birth", "04/17/2015"), ("Sex", "F"),
             ("Parent or guardian", "Alex Sample"), ("Phone", "(555) 010-4471"),
             ("Insurance carrier", "Harborview Health"), ("Member ID", "HHP-77310-02"),
             ("Group number", "G-40115"), ("Allergies", "Penicillin"),
             ("Current medications", "None"), ("Emergency contact", "Jordan Sample (555) 010-4472")]
    lw = int(2.4 * MASTER_DPI)
    for k, v in pairs:
        p.d.text((p.m, p.y + int(0.05 * MASTER_DPI)), k, font=f, fill=90)
        p.d.rectangle([p.m + lw, p.y - 10, W - p.m, p.y + int(fb.size * 1.5)], outline=0, width=3)
        p.d.text((p.m + lw + 25, p.y + 8), v, font=fb, fill=0)
        p.lines.append(f"{k} {v}")
        p.fields.append(v)
        p.y += int(fb.size * 2.3)
    p.space(0.2)
    p.text("Reason for visit", font("sans", 11, True), gap=0.2)
    p.paragraph("Annual well-child checkup and school sports physical. Mild seasonal allergies in spring.",
                font("sans", 11))
    p.space(0.3)
    p.row(["Signature: Alex Sample", "Date: 10/14/2026"], font("sans", 11), [p.m, int(5.0 * MASTER_DPI)],
          fields=(1,))
    return p


def w2_form():
    p = Page(margin_in=0.6)
    p.text("Form W-2  Wage and Tax Statement  2025", font("arial", 15, True), gap=0.3)
    p.text("Copy B - To Be Filed With Employee's FEDERAL Tax Return (sample for testing, not a real form)",
           font("arial", 8.5), gap=0.6)
    boxes = [("a Employee's social security number", "XXX-XX-1234"),
             ("b Employer identification number (EIN)", "00-0012345"),
             ("c Employer's name", "Acme Tools Inc"),
             ("e Employee's name", "Alex Sample"),
             ("1 Wages, tips, other compensation", "63,700.00"),
             ("2 Federal income tax withheld", "7,212.40"),
             ("3 Social security wages", "63,700.00"),
             ("4 Social security tax withheld", "3,949.40"),
             ("5 Medicare wages and tips", "63,700.00"),
             ("6 Medicare tax withheld", "923.65"),
             ("16 State wages, tips, etc.", "63,700.00"),
             ("17 State income tax", "2,548.00")]
    lf, vf = font("arial", 8.5), font("arial", 13, True)
    col_w = (W - 2 * p.m) // 2
    box_h = int(0.62 * MASTER_DPI)
    top = p.y
    for i, (label, val) in enumerate(boxes):
        r, c = divmod(i, 2)
        x0, y0 = p.m + c * col_w, top + r * box_h
        p.d.rectangle([x0, y0, x0 + col_w, y0 + box_h], outline=0, width=3)
        p.d.text((x0 + 20, y0 + 12), label, font=lf, fill=0)
        p.d.text((x0 + 40, y0 + 12 + int(lf.size * 1.6)), val, font=vf, fill=0)
        p.lines.append(f"{label} {val}")
        p.fields.append(val)
    p.y = top + (len(boxes) // 2) * box_h
    return p


DOCS = {"bank_statement": bank_statement, "utility_bill": utility_bill, "insurance_letter": insurance_letter,
        "receipt": receipt, "intake_form": intake_form, "w2_form": w2_form}


def camera_degrade(img, rng):
    """Mild blur, uneven light, noise and JPEG: a deskewed camera capture, not a flatbed scan."""
    a = cv2.GaussianBlur(img.astype(np.float32), (0, 0), 0.7)
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w * rng.uniform(0.35, 0.65), h * rng.uniform(0.3, 0.6)
    light = 1.05 - 0.18 * (((xx - cx) / w) ** 2 + ((yy - cy) / h) ** 2) * 2
    if a.ndim == 3:
        light = light[..., None]
    a = a * light + rng.normal(0, 4, a.shape)
    return np.clip(a, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- historical styles
# What a club's or a family's shoebox holds, as well as modern print: mimeograph, typewriter, newsprint columns, a
# typeset program with a photograph, a handwritten note. Every name, date and place is made up. Each is drawn in
# grey like the pages above (so the ground truth comes from the same layout helper), then given its paper and ink.

def _column(p, s, f, x, width_px, gap=0.22):
    """A wrapped paragraph at ``x``, recorded line by line: columns read top to bottom, then left to right."""
    line = ""
    for word in s.split():
        t = (line + " " + word).strip()
        if p.d.textlength(t, font=f) > width_px and line:
            p.text(line, f, x=x, gap=gap)
            line = word
        else:
            line = t
    if line:
        p.text(line, f, x=x, gap=gap)


def _keys(p, *items):
    p.fields.extend(items)


def mimeo_newsletter():
    p = Page(margin_in=1.0)
    f, fb = font("mono", 11), font("mono", 11, True)
    p.text("RIVERSIDE GARDEN SOCIETY", font("mono", 15, True), gap=0.3)
    p.text("News Letter            Spring 1962            No. 14", fb, gap=0.6)
    p.paragraph("The annual Flower Show will be held Saturday, May 12, at the Grange Hall on Mill Road. Entries "
                "are received from nine until eleven in the morning, and judging begins promptly at noon.", f, 6.4)
    p.space(0.15)
    p.paragraph("Mrs. Eleanor Whitcombe is chairman of the show this year, with Mrs. Arthur Pennell in charge of "
                "the tea table. Members are asked to bring one arrangement and one specimen bloom.", f, 6.4)
    p.space(0.2)
    p.text("COMING EVENTS", fb, gap=0.4)
    for when, what in [("April 10", "Regular meeting, 2 o'clock, at the Library"),
                       ("April 24", "Workshop on forcing bulbs"),
                       ("May 12", "Flower Show, Grange Hall"),
                       ("June 5", "Garden tour and picnic, bring a box lunch")]:
        p.row([when, what], f, [p.m, int(2.7 * MASTER_DPI)], gap=0.4)
    p.space(0.2)
    p.paragraph("The plant sale cleared $86.40, which the board voted to put toward the planting at the station. "
                "Our thanks to the twenty-three members who potted divisions.", f, 6.4)
    _keys(p, "May 12", "Grange Hall", "Eleanor Whitcombe", "Arthur Pennell", "$86.40", "Spring 1962", "No. 14")
    return p


def typed_minutes():
    p = Page(margin_in=1.1)
    f = font("mono", 11.5)
    p.text("MINUTES OF THE REGULAR MEETING", font("mono", 12, True), gap=0.25)
    p.text("October 9, 1951", f, gap=0.7, field=True)
    p.paragraph("The meeting was called to order at 2:15 p.m. by the President, Mrs. Harold Finch, at the home of "
                "Mrs. Walter Ames. Nineteen members answered the roll.", f, 6.2, gap=0.45)
    p.space(0.15)
    p.paragraph("The minutes of the September meeting were read and approved. The Treasurer reported a balance on "
                "hand of $214.37, with all bills paid.", f, 6.2, gap=0.45)
    p.space(0.15)
    p.paragraph("It was moved by Mrs. Ames and seconded by Miss Clara Dunmore that the Society give ten dollars "
                "toward the elm on the Green. The motion carried.", f, 6.2, gap=0.45)
    p.space(0.15)
    p.paragraph("Mrs. Lyle Bassett spoke on the wintering of roses. The meeting adjourned at 4 o'clock.", f, 6.2,
                gap=0.45)
    p.space(0.5)
    p.text("Respectfully submitted,", f, gap=0.45)
    p.text("Clara Dunmore, Secretary", f)
    _keys(p, "Harold Finch", "Walter Ames", "$214.37", "Clara Dunmore", "Lyle Bassett", "2:15 p.m.", "Nineteen")
    return p


def news_clipping():
    p = Page(margin_in=0.9)
    p.text("GARDEN SOCIETY PLANTS 400 BULBS ON TOWN GREEN", font("serif", 19, True), gap=0.3)
    p.text("Members Turn Out Despite Rain; Station Beds Next", font("italic", 12), gap=0.6)
    f = font("serif", 9.5)
    gutter = int(0.22 * MASTER_DPI)
    width = (W - 2 * p.m - 2 * gutter) // 3
    top = p.y
    columns = [
        "RIVERSIDE, Oct. 14 - Two dozen members of the Riverside Garden Society spent Saturday morning setting "
        "four hundred tulip and daffodil bulbs in the beds along the north side of the Town Green. The bulbs were "
        "bought with the proceeds of the spring plant sale. Mrs. Harold Finch, president, said the planting was the "
        "largest the Society has undertaken since it was founded in 1928.",
        "Rain fell for most of the morning, but the work was finished before noon. Selectman George Tarbell sent a "
        "town truck with loam, and boys from the Grange carried water. Coffee was served under the bandstand by "
        "Mrs. Arthur Pennell and her committee. The beds were laid out by Miss Clara Dunmore from a plan drawn the "
        "winter before.",
        "The Society will next replant the two beds at the railroad station, where the old privet is to come out. "
        "Anyone with perennials to spare is asked to leave them at the Library on Elm Street before October 28. "
        "The next regular meeting will be held November 13 at two o'clock, when the speaker will be Mr. Owen Hale "
        "of the County Extension Service.",
    ]
    for i, text in enumerate(columns):
        p.y = top
        _column(p, text, f, p.m + i * (width + gutter), width)
    _keys(p, "Oct. 14", "George Tarbell", "Owen Hale", "1928", "October 28", "November 13", "Elm Street")
    return p


def program_page():
    p = Page(margin_in=1.0)
    centre = W // 2

    def centred(s, f, gap=0.35, field=False):
        p.text(s, f, x=centre - int(p.d.textlength(s, font=f)) // 2, gap=gap, field=field)

    centred("RIVERSIDE GARDEN SOCIETY", font("georgia", 20, True), gap=0.25)
    centred("Year Book  1958 - 1959", font("georgia", 14), gap=0.7, field=True)
    # A halftone-like "photograph": soft tones and a few shapes, no text.
    x0, x1, y0 = int(1.6 * MASTER_DPI), W - int(1.6 * MASTER_DPI), p.y
    y1 = y0 + int(3.3 * MASTER_DPI)
    rng = np.random.default_rng(58)
    tone = cv2.resize(rng.uniform(60, 200, (9, 13)).astype(np.float32), (x1 - x0, y1 - y0),
                      interpolation=cv2.INTER_CUBIC)
    for _ in range(7):
        cx, cy = int(rng.uniform(0.1, 0.9) * (x1 - x0)), int(rng.uniform(0.3, 0.9) * (y1 - y0))
        cv2.ellipse(tone, (cx, cy), (int(0.16 * MASTER_DPI), int(0.42 * MASTER_DPI)), 0, 0, 360,
                    float(rng.uniform(30, 110)), -1)
    p.img.paste(Image.fromarray(np.clip(tone, 0, 255).astype(np.uint8)), (x0, y0))
    p.y = y1 + int(0.12 * MASTER_DPI)
    centred("Members at the Spring Flower Show, May 1958. Left to right: Mrs. Ruth Alden,", font("italic", 9.5),
            gap=0.25)
    centred("Mrs. Lyle Bassett, Miss Clara Dunmore and Mrs. Harold Finch.", font("italic", 9.5), gap=1.1)
    centred("OFFICERS", font("georgia", 13, True), gap=0.5)
    f = font("georgia", 11.5)
    for office, name in [("President", "Mrs. Harold Finch"), ("Vice President", "Mrs. Walter Ames"),
                         ("Recording Secretary", "Miss Clara Dunmore"), ("Treasurer", "Mrs. Ruth Alden"),
                         ("Program Chairman", "Mrs. Eleanor Whitcombe")]:
        p.row([office, name], f, [int(1.9 * MASTER_DPI), W - int(1.9 * MASTER_DPI)], ["l", "r"], gap=0.5)
    p.space(0.3)
    centred("Organized 1928        Federated 1934", font("georgia", 10.5))
    _keys(p, "May 1958", "Ruth Alden", "Harold Finch", "Walter Ames", "Eleanor Whitcombe", "1928", "1934")
    return p


def handwritten_note():
    p = Page(margin_in=1.2)
    f = font("script", 21)
    p.text("June 3, 1947", f, x=int(4.6 * MASTER_DPI), gap=0.9, field=True)
    p.text("Dear Clara,", f, gap=0.7)
    for line in ["Thank you for the slips of the white phlox.", "They have taken well by the south wall.",
                 "I shall bring the minute book on Tuesday", "and the receipts from the plant sale.",
                 "Mrs. Ames has promised her peonies", "for the church on the fifteenth."]:
        p.text(line, f, gap=0.55)
    p.space(0.4)
    p.text("Affectionately,", f, x=int(3.8 * MASTER_DPI), gap=0.6)
    p.text("Ruth Alden", f, x=int(4.2 * MASTER_DPI))
    _keys(p, "Clara", "Ruth Alden", "Tuesday", "Mrs. Ames")
    return p


def _ink_on_paper(density, paper, ink):
    d = np.clip(density, 0, 1)[..., None]
    return np.clip(np.array(paper, np.float32) * (1 - d) + np.array(ink, np.float32) * d, 0, 255).astype(np.uint8)


def _blotches(shape, rng, cells, lo, hi):
    """Low-frequency variation between ``lo`` and ``hi``: uneven inking, foxed or sun-browned paper."""
    small = rng.uniform(lo, hi, cells).astype(np.float32)
    return cv2.resize(small, (shape[1], shape[0]), interpolation=cv2.INTER_CUBIC)


def style_mimeograph(gray, rng):
    """Purple ink that has bled into soft paper, heavier in some places than others."""
    d = cv2.GaussianBlur(1 - gray.astype(np.float32) / 255, (0, 0), 1.6)
    d = np.clip(d * 1.5, 0, 1) * _blotches(gray.shape, rng, (9, 7), 0.45, 1.0)
    d += (rng.random(gray.shape) > 0.9993) * 0.5                    # specks from the stencil
    return _ink_on_paper(d, (236, 229, 210), (96, 62, 138))


def style_typewriter(gray, rng):
    """Carbon-ribbon type: each strike a little lighter or heavier, on bond gone slightly cream."""
    d = cv2.GaussianBlur(1 - gray.astype(np.float32) / 255, (0, 0), 0.9)
    d = np.clip(d * 1.25, 0, 1) * _blotches(gray.shape, rng, (60, 46), 0.6, 1.0)
    return _ink_on_paper(d, (241, 237, 226), (34, 33, 38))


def style_newsprint(gray, rng):
    """Small type on paper that has yellowed unevenly, with the grain showing."""
    d = cv2.GaussianBlur(1 - gray.astype(np.float32) / 255, (0, 0), 0.8) * 0.9
    page = _ink_on_paper(d, (224, 206, 160), (46, 40, 32)).astype(np.float32)
    page *= _blotches(gray.shape, rng, (7, 5), 0.88, 1.03)[..., None]
    page += rng.normal(0, 5, gray.shape)[..., None]
    return np.clip(page, 0, 255).astype(np.uint8)


def style_typeset(gray, rng):
    """Letterpress on ivory stock; the halftone keeps its greys."""
    return _ink_on_paper(1 - gray.astype(np.float32) / 255, (244, 240, 228), (28, 26, 24))


def style_handwriting(gray, rng):
    """Blue-black fountain-pen ink, a little uneven, on notepaper."""
    d = cv2.GaussianBlur(1 - gray.astype(np.float32) / 255, (0, 0), 0.7)
    d = np.clip(d * 1.2, 0, 1) * _blotches(gray.shape, rng, (40, 30), 0.7, 1.0)
    return _ink_on_paper(d, (240, 236, 222), (32, 42, 98))


# name -> (layout, style, material). The material names are the ones a capture bundle's physical.format uses.
HISTORICAL = {
    "mimeo_newsletter": (mimeo_newsletter, style_mimeograph, "mimeograph"),
    "typed_minutes": (typed_minutes, style_typewriter, "typewritten"),
    "news_clipping": (news_clipping, style_newsprint, "newsprint"),
    "program_page": (program_page, style_typeset, "typeset"),
    "handwritten_note": (handwritten_note, style_handwriting, "handwriting"),
}


def render_historical(name, seed=23):
    """One historical page: (the layout with its ground truth, the styled page as an RGB array, its material)."""
    layout, style, material = HISTORICAL[name]
    p = layout()
    rng = np.random.default_rng(seed + sorted(HISTORICAL).index(name))
    return p, style(np.array(p.img), rng), material


def main(out=None):
    out = out or os.environ.get("TESTSET_OUT") or OUT
    rng = np.random.default_rng(11)
    for name, fn in DOCS.items():
        p = fn()
        d = os.path.join(out, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "gt.txt"), "w") as fh:
            fh.write("\n".join(p.lines) + "\n")
        with open(os.path.join(d, "fields.json"), "w") as fh:
            json.dump(p.fields, fh, indent=1)
        master = np.array(p.img)
        for dpi in DPIS:
            size = (int(8.5 * dpi), int(11 * dpi))
            img = cv2.resize(master, size, interpolation=cv2.INTER_AREA)
            cv2.imwrite(os.path.join(d, f"clean_{dpi}.png"), img)
            cv2.imwrite(os.path.join(d, f"camera_{dpi}.jpg"), camera_degrade(img, rng),
                        [cv2.IMWRITE_JPEG_QUALITY, 88])
        print(f"{name}: {len(p.lines)} lines, {len(p.fields)} key fields")
    rng = np.random.default_rng(29)        # its own stream, so the pages above come out exactly as in Phase 0
    for name in HISTORICAL:
        p, master, material = render_historical(name)
        d = os.path.join(out, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "gt.txt"), "w") as fh:
            fh.write("\n".join(p.lines) + "\n")
        with open(os.path.join(d, "fields.json"), "w") as fh:
            json.dump(p.fields, fh, indent=1)
        with open(os.path.join(d, "material.txt"), "w") as fh:
            fh.write(material + "\n")
        for dpi in DPIS:
            img = cv2.resize(master, (int(8.5 * dpi), int(11 * dpi)), interpolation=cv2.INTER_AREA)
            cv2.imwrite(os.path.join(d, f"clean_{dpi}.png"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
            cv2.imwrite(os.path.join(d, f"camera_{dpi}.jpg"),
                        cv2.cvtColor(camera_degrade(img, rng), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
        print(f"{name} ({material}): {len(p.lines)} lines, {len(p.fields)} key fields")


if __name__ == "__main__":
    main()
