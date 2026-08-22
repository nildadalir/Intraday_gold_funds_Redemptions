SELECT
    Instrument,
    AssetId,
    TseId,
    Market
FROM dbo.DimInstrument
where AssetClassId = 3 -- طلا
and IndustryId = 180 -- صندوق
and marketid in (1,6)
and InstrumentId > 0 -- ETF
and Instrument not like '%اختیار%'
and Instrument not like '%شمش%'
