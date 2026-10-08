"""Field map for the original scanned UCS form (minute_agreement_original.pdf).

Its widgets have random names ("Text-gJUsgvzPVi"; fill.py drops the "Text-"), so each one
is mapped to a logical key here by its position on the page. Things the original lacks (signature lines, date of
agreement, fax, case name lines 2-3, a second line for the dates) get a text field that fill.py adds at a fixed
rectangle (OVERLAYS, in PDF points, origin top-left, page 608.4 x 790.2), so they can still be changed in a PDF
viewer.
"""

WIDGETS = {
    "gJUsgvzPVi": "court",
    "IDHqAZVUdT": "county",
    "-fBFyqGk3K": "part",
    "D9jO4QCyJC": "judge",
    "gPkU8gT250": "case_name_1",
    "Chy0IMjsFv": "index_no",
    "hkBcI7tXUi": "dates",
    "bpH7T_2xkr": "proc_arraignment",
    "BW5Be6z3R7": "proc_application",
    "DHxoQ3S1CF": "proc_hearing",
    "Rfmx8ZWCCn": "proc_plea",
    "5-MkrnxI77": "proc_trial",
    "yuBuuMMvhV": "proc_sentence",
    "i2C2tGssy3": "proc_other",
    "FVtEFvDv3v": "rate",
    "KmUKMxYmku": "delivery_regular",
    "KqWQMsrQHJ": "delivery_expedited",
    "tAJApDbRyP": "delivery_daily",
    "JVFXruHvlT": "delivery_other",
    "qoIw1wrlsJ": "copies",
    "66643DIqkw": "est_pages",
    "998Yu0cJbW": "delivery_date",
    "vMAj0k9HiZ": "rep_name",
    "dEslE990LQ": "rep_address_1",
    "46f5KyyHMo": "rep_address_2",
    "Tv8ru2IlXt": "rep_phone",
    "Cycro0ByFv": "atty_name",
    "ug4m1PqYOZ": "atty_firm",
    "zs8meYQ0f0": "atty_address_1",
    "bkM2mficv0": "atty_phone",
}

# Keys with no widget on the original: (x0, y0, x1, y1). dates_2: a second line for the dates, under
# "4. Date(s) of Minutes Requested", for dates that don't fit its blank (fill._spill_dates)
OVERLAYS = {
    "case_name_2": (163, 174, 368, 186),
    "case_name_3": (163, 186, 368, 198),
    "dates_2": (380, 223, 580, 235),
    "sig_reporter": (82, 571, 230, 585),
    "sig_attorney": (268, 573, 415, 587),
    "agreement_date": (455, 573, 565, 587),
    "rep_fax": (132, 659, 312, 671),
    "atty_fax": (395, 661, 565, 673),
}

# Values with no line of their own on the original are appended to a neighbouring one (it has a
# single attorney address line after Firm/Address, for example).
MERGE_INTO = {"atty_address_2": "atty_address_1", "rep_address_3": "rep_address_2"}
