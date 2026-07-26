import os

from pypdf import PdfReader, PdfWriter, Transformation


def scale_and_overlay(agreement_path, estamp_path, output_path):
    if not os.path.exists(agreement_path) or not os.path.exists(estamp_path):
        print("Error: Ensure both 'agreement.pdf' and 'estamp.pdf' are in this folder.")
        return

    reader_agreement = PdfReader(agreement_path)
    reader_estamp = PdfReader(estamp_path)
    writer = PdfWriter()

    # Target legal dimensions in points (72 points per inch)
    legal_width = 612  # 8.5 inches
    legal_height = 1008  # 14 inches

    # Get first pages
    agreement_page = reader_agreement.pages[0]
    estamp_page = reader_estamp.pages[0]

    # Calculate scale factor for height (1008 / 792)
    # Width remains 1.0 since both Letter and Legal are 8.5 inches wide
    scale_y = legal_height / float(estamp_page.mediabox.height)

    # Create transformation matrix to stretch the Letter PDF to Legal size
    op = Transformation().scale(sx=1.0, sy=scale_y)
    estamp_page.add_transformation(op)

    # Explicitly update the canvas boundaries of the stamp page to Legal size
    estamp_page.mediabox.left = 0
    estamp_page.mediabox.bottom = 0
    estamp_page.mediabox.right = legal_width
    estamp_page.mediabox.top = legal_height

    # Merge the stretched stamp layer over your Legal agreement page
    agreement_page.merge_page(estamp_page)
    writer.add_page(agreement_page)

    # Append any remaining pages of the Legal agreement
    if len(reader_agreement.pages) > 1:
        for page_num in range(1, len(reader_agreement.pages)):
            writer.add_page(reader_agreement.pages[page_num])

    with open(output_path, "wb") as output_file:
        writer.write(output_file)

    print(f"Success! Scaled Legal PDF saved as: {output_path}")


scale_and_overlay("agreement.pdf", "estamp.pdf", "final_legal_stamp.pdf")
