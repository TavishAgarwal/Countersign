# Vendor list (generated from switch/vendors.yaml; do not edit)

Use this table for `vendor_id` (match the vendor name on the invoice). The `vendors.yaml` room
document is not available in #ap-intake, so read this file instead. If the invoice's vendor is not
listed, use `V-UNKNOWN`. Compare the invoice's account number with the one below to decide whether
to add a `BANK_CHANGED` flag, and its amount with the baseline for `AMOUNT_ANOMALY` (more than
twice the baseline). Countersign checks all of this itself; your flags are advice.

| vendor_id | name | account on file | routing | baseline (USD) |
|---|---|---|---|---|
| V-ACME | ACME Office Supplies | 7700004412 | 021000021 | 500.00 |
| V-NORTH | Northwind Logistics | 7700007731 | 026009593 | 2,150.00 |
| V-GLOBEX | Globex Cloud Services | 7700002290 | 011000138 | 3,300.00 |
| V-QUICK | QuickExpire Test Co | 7700005005 | 021000021 | 1,200.00 |
