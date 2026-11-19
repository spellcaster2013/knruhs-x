"""Builds a fake KNRUHS-style site in /tmp/mock (listing page + sample notice PDFs) for testing the bot."""
import os, sys, urllib.parse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = "/tmp/mock"; os.makedirs(OUT + "/uploads", exist_ok=True)
HEAD = ["KALOJI NARAYANA RAO UNIVERSITY OF HEALTH SCIENCES", "TELANGANA, WARANGAL"]

NOTICES = {
 "a": ("20261116101530", "KNRUHS - ADMISSIONS - FREE EXIT MDS COURSES CA QUOTA AY 2026-27", [
   "Lr.No.1236/KNRUHS/Admn/PG-MDS/2026                       Date:16.11.2026",
   "Sub:- KNRUHS - Admissions - Notification for free exit to the candidates admitted into MDS courses under",
   "Competent Authority Quota for the academic year 2026-27 - Reg.",
   "",
   "All the candidates admitted up to Mop Up phase of counselling under Competent Authority Quota into MDS courses",
   "into Dental colleges affiliated to KNRUHS are hereby informed that the last date for free exit is on or before",
   "03.00 PM on 17.11.2026. Candidates are permitted to discontinue the course on application to the Principal",
   "for discontinuation from the course without any penalty.",
   "However, the candidates are informed that they shall not be eligible for further rounds of Competent Authority Quota",
   "counselling for MDS courses once they discontinue the course. The seats vacated by the candidates shall be filled",
   "in further phases of counselling.",
   "Principals of Dental colleges are informed to return all the original certificates and entire fee paid by the",
   "candidates immediately and upload the free exit candidates data to the admission portal by 3.30 PM on 17.11.2026.",
   "Candidates are not permitted to withdraw from the course/seat after 03.00 PM on 17.11.2026 onwards.",
   "REGISTRAR"]),
 "b": ("20261116093010", "KNRUHS - EXAMINATIONS - RESULTS OF MDS FIRST YEAR BACKLOG SEPTEMBER 2026", [
   "Lr.No.88/KNRUHS/CoE/MDS-I/2026                          Date:16.11.2026",
   "Sub:- KNRUHS - Examinations - Results of MDS First Year Examinations held in September 2026 (Backlog candidates) - Reg.",
   "",
   "The following is the provisional list of registered numbers of all the successful backlog candidates of MDS First Year",
   "Examinations held in the month of September 2026.",
   "PASSED IN CONSERVATIVE DENTISTRY AND ENDODONTICS: 24206105002, 24206106001, 24206108002 (Total: 3)",
   "PASSED IN ORAL AND MAXILLOFACIAL SURGERY: 24204107001 (Total: 1)",
   "The results are provisional and the candidates are advised to check the university website for updates.",
   "CONTROLLER OF EXAMINATIONS"]),
 "c": ("20261116083000", "KNRUHS - COUNSELLING - PG CQ FIRST PHASE ALLOTMENTS WITHHELD", [
   "Lr.No.412/KNRUHS/PG-CQ/2026/Phase-I                       Date:16.11.2026",
   "Sub:- KNRUHS - Medical PG Degree/Diploma courses - Competent Authority Quota (CQ) for the AY 2026-27 - First phase of counselling - Allotments withheld - Reg.",
   "",
   "It is hereby informed to all the candidates that the University has issued the First Phase allotments for the Competent",
   "Authority Quota (CQ) for the Academic Year 2026-27 on 16.11.2026.",
   "However, due to technical issues, the allotments issued on 16.11.2026 have been withheld. Fresh allotments will be issued",
   "by the University in due course.",
   "In view of the above, candidates are hereby informed not to report to the allotted colleges. The Principals of all Medical",
   "Colleges are also instructed not to admit any candidates based on the said allotments until fresh allotments are issued.",
   "Candidates who have already paid University fee need not pay the fee again for downloading fresh allotment orders."]),
 "d": ("20261115170000", "KNRUHS - ADMISSIONS - BHMS MQ MOP UP WEB OPTIONS", [
   "Lr.No.301/KNRUHS/BHMS/MQ/2026                             Date:15.11.2026",
   "Sub:- KNRUHS - Admission into BHMS course under Management Quota (B & C/NRI) for the AY 2026-27 - Web options for Mop Up phase - Reg.",
   "",
   "The Kaloji Narayana Rao University of Health Sciences hereby notifies to conduct Mop Up phase of web counselling for",
   "admission into BHMS course under Management Quota for the academic year 2026-27 in Private Homoeopathic Medical Colleges.",
   "Eligible candidates in the final merit list for BHMS course are directed to exercise web options from 06.00 AM on 16-11-2026 to",
   "02.00 PM on 17-11-2026 through the website. The details of vacant seats (seat matrix) are available on the website.",
   "Candidates already admitted into MBBS or BDS courses shall not be eligible to participate."]),
 "e": ("20261116110500", "KNRUHS - RECRUITMENT - ASSISTANT PROFESSORS CONTRACT 04 2026", [
   "Notification No.04/2026                                  Date:16.11.2026",
   "Sub:- KNRUHS - Recruitment - Walk-in interview for Assistant Professors on contract basis in Government Medical Colleges - Reg.",
   "",
   "Applications are invited from eligible candidates for the posts of Assistant Professors on contract basis. The walk-in",
   "interview will be held on 24.11.2026 at the university campus, Warangal. Candidates must bring original certificates",
   "and one set of attested copies. The last date for registration of applications is 22.11.2026 up to 5.00 PM."]),
 "f": ("20261116120000", "KNRUHS - CIRCULAR - SCANNED NOTICE", None),  # image-only PDF (no text)
}

def make(key):
    ts, name, lines = NOTICES[key]
    fn = f"{ts}{name}.pdf"
    c = canvas.Canvas(os.path.join(OUT, "uploads", fn), pagesize=A4)
    y = 800
    if lines is None:
        c.rect(60, 500, 470, 240, fill=1)   # a grey box, no text at all
        c.showPage(); c.save(); return fn
    c.setFont("Helvetica-Bold", 12)
    for h in HEAD: c.drawCentredString(297, y, h); y -= 18
    y -= 14; c.setFont("Helvetica", 9.5)
    for ln in lines:
        c.drawString(40, y, ln); y -= 14
    c.showPage(); c.save(); return fn

def listing(keys):
    rows = []
    for k in keys:
        fn = make(k)
        rows.append(f'<li><a href="/uploads/{urllib.parse.quote(fn)}">{NOTICES[k][1].title()}</a></li>')
    html = "<html><body><h1>Latest Notifications</h1><p><a href='/about.html'>About</a></p><ul>" + "".join(rows) + "</ul></body></html>"
    open(os.path.join(OUT, "index.html"), "w").write(html)

if __name__ == "__main__":
    listing(sys.argv[1])
