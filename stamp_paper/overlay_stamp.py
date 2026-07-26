import os
from pypdf import PdfReader, PdfWriter

def overlay_pdfs(agreement_path, estamp_path, output_path):
    # Check if input files exist
    if not os.path.exists(agreement_path) or not os.path.exists(estamp_path):
        print("Error: Please make sure both 'agreement.pdf' and 'estamp.pdf' exist.")
        return

    reader_agreement = PdfReader(agreement_path)
    reader_estamp = PdfReader(estamp_path)
    writer = PdfWriter()

    # Get the first page of both documents
    agreement_page = reader_agreement.pages[0]
    estamp_page = reader_estamp.pages[0]

    # Overlay the e-stamp layout on top of the agreement page
    # This keeps your text underneath the official graphics/borders
    agreement_page.merge_page(estamp_page)
    
    # Add the merged first page to the output
    writer.add_page(agreement_page)

    # If your agreement has more than 1 page, append the rest normally
    if len(reader_agreement.pages) > 1:
        for page_num in range(1, len(reader_agreement.pages)):
            writer.add_page(reader_agreement.pages[page_num])

    # Save the final merged document
    with open(output_path, "wb") as output_file:
        writer.write(output_file)
        
    print(f"Success! Merged PDF saved as: {output_path}")

# Run the function
overlay_pdfs("agreement.pdf", "estamp.pdf", "final_stamped_agreement.pdf")

