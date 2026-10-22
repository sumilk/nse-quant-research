# Data validation

Gate: **PASS**

Calendar: 5407 NSE sessions, 2005-01-03 to 2026-10-22 (NIFTY50 trading days).

| asset       | first      | last       |   sessions |   ffilled |   high_lt_low |   close_outside_hl |   stale_runs |   off_calendar |   extreme_moves |   etf_spikes_removed | dy_from    |   dy_fill_pct |   etf_weekly_corr | etf_tracking_diff_pa   | etf_overlap_from   |   etf_adv_cr |
|:------------|:-----------|:-----------|-----------:|----------:|--------------:|-------------------:|-------------:|---------------:|----------------:|---------------------:|:-----------|--------------:|------------------:|:-----------------------|:-------------------|-------------:|
| NIFTY50     | 2005-01-03 | 2026-10-22 |       5407 |         0 |             0 |                  0 |            0 |              0 |               0 |                    2 | 2015-11-09 |          1.37 |             0.954 | -0.63%                 | 2009-01-02         |       187.72 |
| NEXT50      | 2005-01-03 | 2026-10-22 |       5407 |         0 |             0 |                  0 |            0 |              0 |               0 |                    0 | 2015-11-09 |          1.67 |             0.975 | -1.23%                 | 2009-01-02         |        28.85 |
| MIDCAP150   | 2005-04-01 | 2026-10-22 |       5346 |         0 |             0 |                  0 |            0 |              0 |               0 |                    0 | 2015-01-01 |          1.57 |             0.981 | -0.60%                 | 2019-02-04         |        17.63 |
| SMALLCAP250 | 2005-04-01 | 2026-10-22 |       5346 |         0 |             0 |                  0 |            0 |              0 |               0 |                    0 | 2015-01-01 |          0.99 |             0.991 | -1.58%                 | 2023-02-21         |        25.15 |
| BANK        | 2005-01-03 | 2026-10-22 |       5407 |         0 |             0 |                  0 |            0 |              0 |               0 |                    2 | 2015-11-09 |          1.14 |             0.987 | -0.79%                 | 2009-01-02         |        45.19 |
| IT          | 2005-01-03 | 2026-10-22 |       5407 |         0 |             0 |                  0 |            0 |              0 |               0 |                    0 | 2015-11-09 |          2.38 |             0.996 | -0.72%                 | 2020-06-30         |        44.62 |
| PHARMA      | 2005-01-03 | 2026-10-22 |       5407 |         0 |             0 |                  0 |            0 |              0 |               0 |                    0 | 2015-11-09 |          0.53 |             0.992 | -0.34%                 | 2021-07-07         |        11.23 |
| FMCG        | 2005-01-03 | 2026-10-22 |       5407 |         0 |             0 |                  0 |            0 |              0 |               0 |                    0 | 2015-11-09 |          1.48 |             0.986 | -0.45%                 | 2021-08-10         |         6.48 |
| AUTO        | 2005-01-03 | 2026-10-22 |       5407 |         0 |             0 |                  0 |            0 |              0 |               0 |                    0 | 2015-11-09 |          0.76 |             0.998 | -0.36%                 | 2022-01-24         |         6.67 |
| METAL       | 2011-07-12 | 2026-10-22 |       3785 |         0 |             0 |                  0 |            0 |              0 |               0 |                    0 | 2015-11-09 |          3.75 |             0.994 | -1.92%                 | 2024-08-19         |        12.42 |
| OILGAS      | 2005-04-01 | 2026-10-22 |       5344 |         3 |             0 |                  0 |            0 |              0 |               0 |                    0 | 2020-01-27 |          3.49 |             0.99  | -1.89%                 | 2024-09-02         |         2.47 |
| REALTY      | 2006-12-29 | 2026-10-22 |       4907 |         0 |             0 |                  0 |            0 |              0 |               2 |                    0 | 2015-11-09 |          0.84 |             0.996 | -0.43%                 | 2024-03-18         |         5.41 |
| PSUBANK     | 2005-01-03 | 2026-10-22 |       5397 |        11 |             0 |                  0 |            0 |              0 |               1 |                    2 | 2015-11-09 |          1.93 |             0.962 | -0.95%                 | 2009-01-02         |        26.29 |
| CPSE        | 2005-01-03 | 2026-10-22 |       5407 |         0 |             0 |                  0 |            0 |              0 |               0 |                    0 | 2015-11-09 |          4.02 |             0.979 | -0.49%                 | 2014-03-28         |        10.87 |
| GOLD        | 2009-01-02 | 2026-10-22 |       4410 |        27 |             0 |                  0 |            0 |              2 |               0 |                    2 | n/a        |        nan    |           nan     | nan                    | nan                |       345.97 |
| SILVER      | 2022-02-04 | 2026-10-22 |       1167 |         5 |             0 |                  0 |            0 |              1 |               1 |                    0 | n/a        |        nan    |           nan     | nan                    | nan                |       647.46 |
| GSEC10Y     | 2005-01-03 | 2026-10-22 |       5407 |         1 |             0 |                409 |            0 |              0 |               0 |                    0 | n/a        |        nan    |             0.732 | +0.69%                 | 2018-05-11         |         4.66 |
| CASH        | 2011-01-03 | 2026-10-22 |       3916 |        58 |             0 |                  0 |            0 |              0 |               0 |                    0 | n/a        |        nan    |           nan     | nan                    | nan                |       316.76 |

## Extreme daily moves (> 20%)

- REALTY 2008-10-24: -23.7% — Global financial crisis sell-off (NIFTY -6% that day)
- REALTY 2009-05-18: +23.7% — General-election result rally; market hit upper circuit
- PSUBANK 2017-10-25: +29.6% — Rs 2.11 lakh crore PSU bank recapitalisation announced 24-Oct-2017
- SILVER 2026-02-02: -23.2% — Global silver crash at end of Jan 2026 after a parabolic rally

## ETF prints removed (more than 3x from the 11-session median)

- NIFTYBEES: 2019-12-19, 2019-12-20
- BANKBEES: 2019-12-19, 2019-12-20
- PSUBNKBEES: 2019-12-19, 2019-12-20
- GOLDBEES: 2019-12-19, 2019-12-20

## Failures

- none

## Waived (documented in config data_notes.waivers)

- GSEC10Y: index-ETF weekly correlation 0.732 < 0.95 — waived: LTGILTBEES tracks NIFTY 8-13 yr G-Sec, not the 10 yr benchmark index; weekly corr 0.73
- GSEC10Y: OHLC inconsistencies — waived: Close is the end-of-day valuation, high/low are traded prices; median gap 0.05%

## Notes

- `dy_from`: first session with a published NSE dividend yield. Earlier sessions use `dy_fill_pct`, the median yield over the first published year (approximation).
- `ffilled`: sessions missing after the first price and carried forward (max 2 per gap).
- `off_calendar`: source rows on dates that are not NIFTY 50 sessions (dropped).
- ETF ADV is the median daily traded value over the last year: it sets spread tiers, impact and capacity, and is applied to all history (a known approximation).
