"""Do test PDFs banata hai jo real hospital forms jaise dikhte hain."""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent
W, H = A4
LEFT = 45
RIGHT = W - 45


def blank(c, x, y, width=90):
    """Ruled blank (underline) — real forms mein aisa hi hota hai."""
    c.setLineWidth(0.6)
    c.line(x, y - 2, x + width, y - 2)
    return x + width


def checkbox(c, x, y, size=9):
    c.setLineWidth(0.8)
    c.rect(x, y - 1, size, size, stroke=1, fill=0)
    return x + size


# ---------------------------------------------------------------------------
def patient_information_form():
    path = OUT / "patient_information_form.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    y = H - 60

    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(W / 2, y, "PATIENT INFORMATION FORM")
    y -= 26

    c.setFont("Helvetica", 10)
    c.drawString(LEFT, y, "Dear Patient,")
    y -= 14
    c.drawString(LEFT, y, "The following information is needed to help us to plan for your study.")
    y -= 22

    c.drawString(LEFT, y, "1. Please list all the current medications you are taking")
    blank(c, LEFT + 285, y, 150)
    y -= 22

    c.drawString(LEFT, y, "2. Are you currently taking oral diabetic drugs containing Metformin")
    c.drawRightString(RIGHT - 30, y, "Yes")
    c.drawRightString(RIGHT, y, "No")
    y -= 14
    c.setFont("Helvetica", 9)
    c.drawString(LEFT + 12, y, "(Exmet SR, Metlong, Metaday, Glyciphage SR, Gluformin)")
    y -= 20

    c.setFont("Helvetica", 10)
    c.drawString(LEFT, y, "3. Are you pregnant, or have you recently delivered a child?")
    c.drawRightString(RIGHT - 60, y, "Not sure")
    c.drawRightString(RIGHT - 30, y, "Yes")
    c.drawRightString(RIGHT, y, "No")
    y -= 20

    c.drawString(LEFT, y, "4. Last Menstrual period")
    blank(c, LEFT + 130, y, 120)
    y -= 20

    c.drawString(LEFT, y, "5. Are you currently breast feeding/nursing?")
    c.drawRightString(RIGHT - 60, y, "Not sure")
    c.drawRightString(RIGHT - 30, y, "Yes")
    c.drawRightString(RIGHT, y, "No")
    y -= 20

    c.drawString(LEFT, y, "6. Have you eaten in the last 4 hours?")
    c.drawRightString(RIGHT - 30, y, "Yes")
    c.drawRightString(RIGHT, y, "No")
    y -= 20

    c.drawString(LEFT, y, "7. Do you have any allergies to food or medicine?")
    c.drawRightString(RIGHT - 30, y, "Yes")
    c.drawRightString(RIGHT, y, "No")
    y -= 26

    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(LEFT, y, "MEDICAL CONDITIONS")
    y -= 18
    c.setFont("Helvetica", 10)
    c.drawString(LEFT, y, "8. Do you have any of the following conditions?")
    y -= 16
    for label in [
        "a. Bronchial Asthma",
        "b. High Blood Pressure (Hypertension)",
        "c. Diabetes Mellitus (high blood sugar)",
        "d. Kidney dysfunction (creatinine greater than 1.4 mg/dl)",
        "e. Sickle Cell Anemia",
        "f. Multiple Myeloma (a disease of the bone marrow)",
    ]:
        c.drawString(LEFT + 14, y, label)
        c.drawRightString(RIGHT - 30, y, "Yes")
        c.drawRightString(RIGHT, y, "No")
        y -= 15
    y -= 12

    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(LEFT, y, "RECENT BLOOD INVESTIGATIONS")
    y -= 20
    c.setFont("Helvetica", 10)
    c.drawString(LEFT, y, "Serum creatinine")
    blank(c, LEFT + 100, y, 90)
    y -= 22
    x = LEFT
    for lab in ["PT", "PTT", "CT", "BT", "INR"]:
        c.drawString(x, y, lab)
        x = blank(c, x + 22, y, 55) + 14
    y -= 34

    c.drawString(LEFT, y, "Date:")
    blank(c, LEFT + 32, y, 120)
    c.drawString(LEFT + 230, y, "Patient ID:")
    blank(c, LEFT + 290, y, 140)

    c.showPage()
    c.save()
    return path


# ---------------------------------------------------------------------------
def anaesthesia_consent_form():
    """KareXpert screenshot wale form ka PDF version — real checkbox squares ke saath."""
    path = OUT / "informed_consent_anaesthesia.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    y = H - 55

    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(W / 2, y, "INFORMED CONSENT FOR ANAESTHESIA")
    y -= 30

    c.setFont("Helvetica", 10)
    c.drawString(LEFT, y, "Title of the procedure/ surgery contemplated:")
    blank(c, LEFT + 225, y, 250)
    y -= 30

    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(LEFT, y, "TYPE OF ANAESTHESIA RECOMMENDED")
    y -= 20
    c.setFont("Helvetica", 10)
    x = LEFT
    for opt in ["General Anaesthesia", "Spinal/ Epidural Combined", "Nerve Block"]:
        x = checkbox(c, x, y) + 6
        c.drawString(x, y, opt)
        x += c.stringWidth(opt, "Helvetica", 10) + 18
    y -= 18
    x = LEFT
    for opt in ["Monitored Anaesthesia Care", "Sedation", "Local"]:
        x = checkbox(c, x, y) + 6
        c.drawString(x, y, opt)
        x += c.stringWidth(opt, "Helvetica", 10) + 18
    y -= 32

    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(LEFT, y, "PRE-ANAESTHETIC ASSESSMENT")
    y -= 20
    c.setFont("Helvetica", 10)
    c.drawString(LEFT, y, "Weight")
    blank(c, LEFT + 45, y, 60)
    c.drawString(LEFT + 130, y, "Pulse")
    blank(c, LEFT + 165, y, 60)
    c.drawString(LEFT + 250, y, "BP")
    blank(c, LEFT + 272, y, 60)
    y -= 22
    c.drawString(LEFT, y, "Diagnosis")
    blank(c, LEFT + 58, y, 400)
    y -= 30

    c.drawString(LEFT, y, "Has the patient given consent for blood transfusion?")
    c.drawRightString(RIGHT - 30, y, "Yes")
    c.drawRightString(RIGHT, y, "No")
    y -= 20
    c.drawString(LEFT, y, "Is the patient a known case of drug allergy?")
    c.drawRightString(RIGHT - 30, y, "Yes")
    c.drawRightString(RIGHT, y, "No")
    y -= 30

    # ---- ruled table ----
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(LEFT, y, "MEDICATIONS ADMINISTERED")
    y -= 8
    rows = [
        ["Drug", "Dose", "Route", "Time"],
        ["", "", "", ""],
        ["", "", "", ""],
        ["", "", "", ""],
    ]
    col_w = [180, 90, 90, 90]
    row_h = 18
    top = y - 6
    c.setFont("Helvetica", 9.5)
    c.setLineWidth(0.7)
    for r, row in enumerate(rows):
        x = LEFT
        for i, cell in enumerate(row):
            c.rect(x, top - (r + 1) * row_h, col_w[i], row_h, stroke=1, fill=0)
            if cell:
                c.setFont("Helvetica-Bold", 9.5)
                c.drawString(x + 4, top - (r + 1) * row_h + 5, cell)
                c.setFont("Helvetica", 9.5)
            x += col_w[i]
    y = top - len(rows) * row_h - 28

    c.setFont("Helvetica", 9.5)
    c.drawString(LEFT, y, "I have explained the nature of the anaesthesia, its risks and alternatives to the")
    y -= 13
    c.drawString(LEFT, y, "patient in a language that he/she understands. The patient has consented freely.")
    y -= 34

    c.setFont("Helvetica", 10)
    c.drawString(LEFT, y, "Signature of Anaesthetist:")
    blank(c, LEFT + 135, y, 160)
    y -= 26
    c.drawString(LEFT, y, "Signature of Patient/ Attendant:")
    blank(c, LEFT + 165, y, 160)
    y -= 26
    c.drawString(LEFT, y, "Date:")
    blank(c, LEFT + 32, y, 110)
    c.drawString(LEFT + 200, y, "Time:")
    blank(c, LEFT + 235, y, 110)

    c.showPage()
    c.save()
    return path


if __name__ == "__main__":
    for p in (patient_information_form(), anaesthesia_consent_form()):
        print("wrote", p)
