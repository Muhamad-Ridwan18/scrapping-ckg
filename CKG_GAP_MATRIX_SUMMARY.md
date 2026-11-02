# Matriks Gap: SIMPUS CKG×ILP ↔ ckg-ai ASIK

## Scope

- **SIMPUS Umum:** CSV master `klikmedisapp/public/skrining-ckg-ilp/sheet-*.csv` (7 band usia ILP).
- **ckg-ai Umum:** `backend/app/data/asik_form_mapping.json` (FRM / pertanyaan ASIK).
- **Sekolah:** bandingkan `asik_sekolah_form_catalog.json` di kedua repo.

Matching pertanyaan: normalisasi teks + exact, lalu fuzzy ≥ 86% (bukan kode FRM — ILP CSV belum punya FRM/PPM).

## Ringkasan Umum (ILP → ASIK)

| Metrik | Nilai |
|---|---:|
| Pertanyaan ILP (CKG/ILP rows) | 740 |
| Matched ke ASIK | 420 (57%) |
| ILP tanpa pasangan ASIK | 320 |
| Pertanyaan ASIK total | 565 |
| ASIK yang ketemu di ILP | 158 (28%) |
| ASIK belum ada di ILP | 407 |

### Coverage per band usia ILP

| Grup ILP | Pertanyaan | Matched | Coverage |
|---|---:|---:|---:|
| `bayi_baru_lahir` | 5 | 4 | 80% |
| `bayi_2_11_bulan` | 37 | 11 | 30% |
| `anak_1_6` | 56 | 20 | 36% |
| `remaja_7_17` | 89 | 55 | 62% |
| `dewasa_18_39` | 173 | 82 | 47% |
| `dewasa_40_59` | 194 | 107 | 55% |
| `lansia` | 186 | 141 | 76% |

### Coverage per form ASIK (FRM)

| Bucket | Jumlah form |
|---|---:|
| mostly_covered (≥80% Q matched) | 23 |
| partial | 38 |
| missing_in_ilp (0 Q) | 55 |

## Sekolah

| Metrik | Nilai |
|---|---:|
| Form di ckg-ai catalog | 43 |
| Form di SIMPUS catalog | 43 |
| FRM di kedua sisi | 43 |
| Hanya ckg-ai | 0 |
| Hanya SIMPUS | 0 |

## File output

| File | Isi |
|---|---|
| `CKG_GAP_MATRIX_questions.csv` | Setiap pertanyaan ILP + pasangan ASIK (atau gap) |
| `CKG_GAP_MATRIX_asik_only_questions.csv` | Pertanyaan ASIK yang belum ada di ILP |
| `CKG_GAP_MATRIX_asik_forms.csv` | Rollup per FRM (coverage %) |
| `CKG_GAP_MATRIX_sections.csv` | Section ILP vs judul layanan/paket ASIK |
| `CKG_GAP_MATRIX_sekolah.csv` | Gap FRM/Q katalog sekolah |

## Cara baca (prioritas kerja)

1. `missing_in_ilp` di `CKG_GAP_MATRIX_asik_forms.csv` → form ASIK yang belum di SIMPUS.
2. `only_simpus` di questions → field ILP yang tidak ada di mapping ASIK (custom / label drift / embed).
3. Sekolah: baris `only_ckg_ai` / beda `q_only_*` → sync catalog SIMPUS ke ckg-ai.
4. Jangka panjang: seed master SIMPUS pakai `frm_code` + `parameter_codes`, jangan hanya label.
