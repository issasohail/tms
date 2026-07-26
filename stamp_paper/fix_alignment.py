import os

from pypdf import PdfReader, PdfWriter, Transformation


def scale_shift_and_overlay(agreement_path, estamp_path, output_path):
    if not os.path.exists(agreement_path) or not os.path.exists(estamp_path):
        print("Error: Ensure both input files exist in this folder.")
        return

    reader_agreement = PdfReader(agreement_path)
    reader_estamp = PdfReader(estamp_path)
    writer = PdfWriter()

    legal_width = 612  # 8.5 inches
    legal_height = 1008  # 14 inches

    agreement_page = reader_agreement.pages[0]
    estamp_page = reader_estamp.pages[0]

    # Step 1: Shift the agreement text DOWN by 1 inch (72 points) to clear the header
    # 72 points equals exactly 1.0 inch
    shift_agreement = Transformation().translate(tx=0, ty=-72)
    agreement_page.add_transformation(shift_agreement)

    # Step 2: Scale the e-stamp to Legal size height
    scale_y = legal_height / float(estamp_page.mediabox.height)
    scale_stamp = Transformation().scale(sx=1.0, sy=scale_y)
    estamp_page.add_transformation(scale_stamp)

    # Update boundaries for the stamp canvas
    estamp_page.mediabox.left = 0
    estamp_page.mediabox.bottom = 0
    estamp_page.mediabox.right = legal_width
    estamp_page.mediabox.top = legal_height

    # Step 3: Merge layers together
    agreement_page.merge_page(estamp_page)
    writer.add_page(agreement_page)

    # Append any remaining pages of the agreement normally
    if len(reader_agreement.pages) > 1:
        for page_num in range(1, len(reader_agreement.pages)):
            writer.add_page(reader_agreement.pages[page_num])

    with open(output_path, "wb") as output_file:
        writer.write(output_file)

    print(f"Success! Aligned Legal PDF saved as: {output_path}")


scale_shift_and_overlay("agreement.pdf", "estamp.pdf", "final_legal_stamp.pdf")
