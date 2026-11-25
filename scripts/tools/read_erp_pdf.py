# -*- coding: utf-8 -*-
"""读取ERP导出的PDF报表"""
import _bootstrap  # noqa: F401  # ensure project paths
import pdfplumber
import json
import os

pdf_path = r"D:\Downloads\Report_MRB_Day2604172008.pdf"
output_path = r"d:\AI助理\PCB副总经理助理系统\品质分析处理系统\erp_pdf_content.txt"

print("Reading PDF...")

content = []

with pdfplumber.open(pdf_path) as pdf:
    print(f"Total pages: {len(pdf.pages)}")
    content.append(f"Total pages: {len(pdf.pages)}\n")
    
    for i, page in enumerate(pdf.pages):
        content.append(f"\n--- Page {i+1} ---\n")
        text = page.extract_text()
        if text:
            content.append(text)
            content.append("\n")
        
        tables = page.extract_tables()
        if tables:
            content.append(f"\n[Found {len(tables)} tables]\n")
            for j, table in enumerate(tables):
                content.append(f"\nTable {j+1}:\n")
                for row in table[:30]:
                    content.append(str(row) + "\n")

# Write to file with UTF-8
with open(output_path, 'w', encoding='utf-8') as f:
    f.writelines(content)

print(f"Content saved to: {output_path}")
print("Done!")
