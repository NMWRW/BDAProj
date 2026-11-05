#Code created with help from ChatGPT

from bs4 import BeautifulSoup
import unicodedata
import re
import pandas as pd
import os

#Get the text from the HTML/xml file
file_path = r"data\sec\CIK0000320193\20261030T012745363115Z-8b6e805e\0000320193-26-000020_aapl-20260627.htm"
filename = os.path.basename(file_path)
with open(file_path, "r", encoding="utf-8") as file:
    soup = BeautifulSoup(file, "xml") 
    
text = soup.get_text(separator="\n", strip=True)

# Normalize Unicode characters
text = unicodedata.normalize("NFKC", text)

# Replace nonbreaking spaces and other whitespace with regular spaces
text = text.replace("\xa0", " ")
text = re.sub(r"\s+", " ", text)

# Start search after Table of contents to avoid selecting from it
startMatch = re.search(r"FINANCIAL INFORMATION", text, re.IGNORECASE)
if startMatch:
    text = text[startMatch.start():]
    
#List of Sections - item 6 while not useful on its own is included so item 5 stops properly
sections = [
    "Item 1. Financial Statements",
    "Item 2. Management’s Discussion and Analysis of Financial Condition and Results of Operations",
    "Item 3. Quantitative and Qualitative Disclosures About Market Risk",
    "Item 4. Controls and Procedures",
    "Item 1. Legal Proceedings",
    "Item 1A. Risk Factors",
    "Item 2. Unregistered Sales of Equity Securities and Use of Proceeds",
    "Item 3. Defaults Upon Senior Securities",
    "Item 4. Mine Safety Disclosures",
    "Item 5. Other Information",
    "Item 6. Exhibits"
]
section_positions = {}
section_text = {}

for section in sections:
    # Escape special regex characters and allow flexible whitespace
    pattern = re.escape(section)
    pattern = pattern.replace(r"\ ", r"\s+")

    matches = re.search(pattern, text, re.IGNORECASE)   
      
    if matches:
        section_positions[section] = matches.start()
    else: 
        section_text[section] = (f"Item not found in the text.")
        

sorted_sections = sorted(
    section_positions.items(),
    key=lambda x: x[1]
)

# Match text to sections 
for i, (section, start) in enumerate(sorted_sections):
    
    if i + 1 < len(sorted_sections):
        end = sorted_sections[i + 1][1]
    else:
        end = len(text)

    section_text[section] = text[start:end]

    #print(f"\n--- {section} ---")
    #print(section_text[section])
    
#Save as CSV    
df = pd.DataFrame(
    section_text.items(),
    columns=["Section", "Text"]
)

df.to_csv(f"{filename}_filing_sections.csv", index=False)