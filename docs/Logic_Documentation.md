# Logic Documentation

> **Role of this document:** Authoritative business rules. Implementation must follow these. Structure lives in [`Architecture.md`](Architecture.md). Pipeline in [`Orchestration.md`](Orchestration.md).

| Field | Value |
| --- | --- |
| Document status | Current |
| Last updated | 2026-09-20 |
| Related documents | [`Architecture.md`](Architecture.md) |

---

## 1. Instrument grouping

Rows are grouped by **AssetId**.

- **Main** instrument: name does not end with `2` / `3` / `4`, and `Market` contains `اصلی`.
- **Market** instrument (legal volume): name ends with `2`, or `Market` contains `آد`. Prefer an exact `*2` name.

Funds with no market board are skipped (not a structural error). Missing main board or missing `TseId` is a structural error on the HTML Error Summary.

When the list comes from SQL, `Asset` and `InstrumentId` are filled from `Instrument` / `AssetId` (the query does not return a separate Asset column).

---

## 2. TSETMC fields

| Need | Endpoint | Instrument |
| --- | --- | --- |
| Last trade | `/api/ClosingPrice/GetClosingPriceInfo/{TseId}` (`pDrCotVal` / `pl` / `pClosing`) | Main (first market / اصلی) |
| قیمت پایانی | Same `GetClosingPriceInfo` (`pClosing`) | Main (first market / اصلی) |
| NAV redemption / issue | `/api/Fund/GetETFByInsCode/{TseId}` (`pRedTran`, `pSubTran`) | Main |
| حقوقی volume | `/api/ClientType/GetClientType/{TseId}/1/0` (`buy_N_Volume`) | Market (`*2`) |

Last trade is **only** used to classify. The report **Price** column is `pClosing` (قیمت پایانی) from the first market. The report **NAV** column is the selected NAV (redemption or issue), not last trade.

Internal units from TSETMC are **Rial**.

---

## 3. Classification and value

- If **Last ≤ NAV Redemption** → category **Redemption** → selected price = NAV Redemption  
- If **Last > NAV Redemption** → category **Issue/Redemption** → selected price = Issue NAV (error if issue NAV missing)

**Institutional value (Rial)** = `buy_N_Volume ×` selected NAV.

If live legal buy volume is 0, value is 0 (page value). Buy/sell mismatch logs a warning; buy is used.

---

## 4. Report display units

| Column | Display |
| --- | --- |
| Price | Rial / IRR (integer); `pClosing` on the first market |
| NAV | Rial / IRR (integer) |
| Institutional volume | Unit count (not converted) |
| Institutional value | Billion Rial / IRR = Rial / 1e9 |

Header date and “generated at” are **Jalali** with English digits. Output **filename** stays Gregorian.

The Error Summary block is omitted when there are no errors.

---

## 5. All-zero and market hours

If every fund’s calculated value is 0 (or nothing was valued), the HTML is written to **`output_error/`** and **email is not sent**.

Typical causes: Thursday/Friday, or Saturday–Wednesday **before 12:00** or **from 18:00** Tehran time, or an open session with no حقوقی volume.

Hours (config `market`): Saturday–Wednesday, **12:00 inclusive – 18:00 exclusive**, `Asia/Tehran`. Hours explain the log (`gold market open=true/false`); they do not skip the job.
