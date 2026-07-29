import os

from pypdf import PdfReader, PdfWriter, Transformation


def align_estamp_top(agreement_path, estamp_path, output_path):
    if not os.path.exists(agreement_path) or not os.path.exists(estamp_path):
        print("Error: Ensure both input files exist in this folder.")
        return

    reader_agreement = PdfReader(agreement_path)
    reader_estamp = PdfReader(estamp_path)
    writer = PdfWriter()

    # Define standard Legal page metrics
    legal_width = 612  # 8.5 inches
    legal_height = 1008  # 14 inches

    agreement_page = reader_agreement.pages[0]
    estamp_page = reader_estamp.pages[0]

    # Calculate exact vertical scaling factor needed
    scale_y = legal_height / float(estamp_page.mediabox.height)

    # Fit the e-stamp to the canvas size without adding translation clipping
    adjust_stamp = Transformation().scale(sx=1.0, sy=scale_y)
    estamp_page.add_transformation(adjust_stamp)

    # Define bounding box dimensions to prevent canvas clipping
    estamp_page.mediabox.left = 0
    estamp_page.mediabox.bottom = 0
    estamp_page.mediabox.right = legal_width
    estamp_page.mediabox.top = legal_height

    # Merge layers: Put agreement text onto the scaled background stamp canvas
    # This preserves the entire top header safely
    estamp_page.merge_page(agreement_page)
    Start it with:
    writer.add_page(estamp_page)

    # Append subsequent document pages normally
    if len(reader_agreement.pages) > 1:
        for page_num in range(1, len(reader_agreement.pages)):
            writer.add_page(reader_agreement.pages[page_num])

    with open(output_path, "wb") as output_file:
        writer.write(output_file)

    print(f"Success! Perfect alignment with full graphics saved to: {output_path}")


align_estamp_top("agreement.pdf", "estamp.pdf", "final_legal_stamp.pdf")
