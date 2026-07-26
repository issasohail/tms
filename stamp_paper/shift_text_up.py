import os

from pypdf import PdfReader, PdfWriter, Transformation


def shift_estamp_only(agreement_path, estamp_path, output_path):
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

    # Step 1: Scale the e-stamp layout to Legal height
    scale_y = legal_height / float(estamp_page.mediabox.height)

    # Step 2: Apply the scaling AND translate the e-stamp graphics UP
    # ty=45 points shifts the e-stamp header up by roughly 0.6 inches
    adjust_stamp = Transformation().scale(sx=1.0, sy=scale_y).translate(tx=0, ty=45)
    estamp_page.add_transformation(adjust_stamp)

    # Lock canvas coordinate bounds to Legal dimensions
    estamp_page.mediabox.left = 0
    estamp_page.mediabox.bottom = 0
    estamp_page.mediabox.right = legal_width
    estamp_page.mediabox.top = legal_height

    # Step 3: Merge layers (Agreement text stays put, shifted e-stamp sits over it)
    agreement_page.merge_page(estamp_page)
    writer.add_page(agreement_page)

    # Append all remaining agreement pages unchanged
    if len(reader_agreement.pages) > 1:
        for page_num in range(1, len(reader_agreement.pages)):
            writer.add_page(reader_agreement.pages[page_num])

    with open(output_path, "wb") as output_file:
        writer.write(output_file)

    print(f"Success! Adjusted e-stamp position safely: {output_path}")


shift_estamp_only("agreement.pdf", "estamp.pdf", "final_legal_stamp.pdf")
