"""Field map for the UCS Minute Order Form/Receipt (mofr.pdf, form MOFR-S CRTS/GA.0 REV 8/12).

The form's fields are named "Text Field0".."Text Field35" and "Check Box0".."Check Box12"; each is
mapped here by the label it sits on. Only the reporter's parts are filled: the header, Section I
and the page count in Section III. The judge, counsel, clerk and auditor fill the rest.
"""

TEXT = {
    "Text Field0": "county",        # ____ SUPREME COURT
    "Text Field1": "title",         # TITLE OF ACTION: PEOPLE V ____
    "Text Field3": "rep_name",      # COURT REPORTER(S) (Print)
    "Text Field4": "rep_location",  # COURT REPORTER(S) (Location)
    "Text Field5": "index_no",      # INDICTMENT NUMBER
    "Text Field7": "part",          # PART NUMBER
    "Text Field8": "judge",         # JUDGE ____ J.S.C.
    "Text Field9": "dates",         # DATE(S) OF PROCEEDING
    "Text Field34": "copies",       # TOTAL COPIES
    "Text Field35": "proc_other",   # Other Proceeding: (describe)
    "Text Field12": "pages",        # Section III: consisting of ___ pages
}

CHECKS = {
    "Check Box0": "criminal",            # header: the division (Settings.mofr_division)
    "Check Box1": "civil",
    "Check Box3": "daily",               # Section I type of order: DAILY (NEXT DAY DELIVERY)
    "Check Box4": "expedited",           # EXPEDITE (THREE DAY DELIVERY)
    "Check Box5": "proc_sentence",
    "Check Box6": "proc_plea",
    "Check Box7": "proc_other_check",
    "Check Box10": "daily",              # Section III: DAILY
    "Check Box11": "expedited",          # EXPEDITE (3 DAY DEL.)
    "Check Box12": "regular",            # REGULAR (FOR 18B)
}

# The pre-printed "PEOPLE V" before the title line; covered on civil forms: (x0, y0, x1, y1)
PEOPLE_V = (141, 136.5, 203, 150.5)
