# BPI Challenge 2019 sample

`bpi2019_sample.csv` is 800 cases (200 per item category, 6,502 events) of
the BPI Challenge 2019 purchase-to-pay event log, with five columns:
case id, activity, timestamp (UTC), resource and item category.

It is cut deterministically by `scripts/make_bpi_sample.py` (cases ordered by
the SHA-256 of their id within each category, after the preset's suggested
cleaning) and is read through the `bpi2019_sample` preset.

**Source.** van Dongen, B.F. (2019). *BPI Challenge 2019*. 4TU.ResearchData.
https://doi.org/10.4121/uuid:d06aff4b-79f0-45e6-8ec8-e19730c248f1

The sample is redistributed for reproducibility of a bachelor's thesis. The
terms of use of the original dataset (see the DOI above) apply to it; cite
the source when using it.
