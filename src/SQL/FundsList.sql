-- Listed gold ETFs (cash + related boards). Environment-specific IDs belong in the
-- connected warehouse; this file is the filter used when db.use_db is true.
SELECT
    Instrument,
    AssetId,
    TseId,
    Market
FROM dbo.DimInstrument
where AssetClassId = 3 -- gold
and IndustryId = 180 -- fund
and marketid in (1,6)
and InstrumentId > 0 -- ETF
and Instrument not like '%اختیار%'
and Instrument not like '%شمش%'
