"""Field map for the UCS fillable form (minute_agreement_ucs.pdf, made by tools/prepare_ucs_form.py).

Several of the form's own field names don't match the label they sit on, so each is mapped by
its position on the page:
  "1" is the court blank, "Court" is the county blank, "FirmAddress" is Name of Attorney/Party,
  "Address3" is the Firm/Address line and "Address4" the line under it.
The form has no fax lines. The two signature spots, a second line for the dates and a line saying which pages
are at which speed get text fields added by fill.py (OVERLAYS).
"""

WIDGETS = {
    "1": "court",
    "Court": "county",
    "Part No": "part",
    "Name of JudgeJustice": "judge",
    "2 Name of Case 1": "case_name_1",
    "2 Name of Case 2": "case_name_2",
    "2 Name of Case 3": "case_name_3",
    "3 Court DocketFileIndex Number": "index_no",
    "4 Datess of Minutes Requested": "dates",
    "Arraignment": "proc_arraignment",
    "Application": "proc_application",
    "Hearing": "proc_hearing",
    "Plea": "proc_plea",
    "Trial": "proc_trial",
    "Sentence": "proc_sentence",
    "Other specify": "proc_other",
    "7 Rate to be Charged Per Page": "rate",
    "Regular": "delivery_regular",
    "Expedited": "delivery_expedited",
    "Daily": "delivery_daily",
    "Other": "delivery_other",
    "No of Copies Ordered": "copies",
    "8 Estimated Number of Pages": "est_pages",
    "9 Estimated Delivery Date": "delivery_date",
    "Date of Agreement": "agreement_date",
    "Name of Court Reporter 1": "rep_name",
    "Address1": "rep_address_1",
    "Address2": "rep_address_2",
    "phone1": "rep_phone",
    "email address1": "rep_email",
    "FirmAddress": "atty_name",
    "Address3": "atty_firm",
    "Address4": "atty_address_1",
    "phone2": "atty_phone",
    "Email address_2": "atty_email",
}

# Lines the form lacks, as (x0, y0, x1, y1): the signature lines (the court's copy had Adobe signature fields
# here), a second line for the dates, under "4. Date(s) of Minutes Requested", for dates that don't fit the
# form's 100-point blank (fill._spill_dates), and, beside "No. of Copies Ordered", which pages are at which
# speed on an agreement covering two (the rate line then says "see below": fill.speeds_note)
OVERLAYS = {
    "sig_reporter": (58, 622, 220, 636),
    "sig_attorney": (255, 622, 417, 636),
    "dates_2": (344, 264, 566, 277),
    "speeds_note": (240, 551, 576, 568),
}

# Values with no line of their own on this form are appended to a neighbouring one
MERGE_INTO = {"atty_address_2": "atty_address_1", "rep_address_3": "rep_address_2"}

# Page 2 holds the official instructions (fill.py drops it when Settings.include_instructions is off)
INSTRUCTION_PAGES = 1
