# Invoice exceptions report

Processed 6 invoices. Exceptions are held back from the ERP import file.

| File | Invoice No | Status | Reasons |
|---|---|---|---|
| inv03_coromandel.pdf | CPC-7781 | EXCEPTION | Line 2 'Kraft paper tape roll': 120 x 65.00 = 7800.00, invoice shows 8500.00<br>Grand total 35000.00 != taxable 29750.00 + tax 5355.00 (35105.00)<br>Low confidence on 'place_of_supply' (0.60) |
| inv04_yamuna.pdf | YOS/1029 | EXCEPTION | Supplier: GSTIN '07ABCPY3456R1ZZ' fails checksum (expected check char 'S', found 'Z') |
| inv05_kaveri_resend.pdf | KPT/2026-27/0142 | EXCEPTION | Duplicate of inv01_kaveri.pdf (same supplier GSTIN + invoice no + FY 2026-27) |
