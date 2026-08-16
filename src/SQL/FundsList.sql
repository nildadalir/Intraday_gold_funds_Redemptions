select distinct TseId from DimInstrument 
where 
	IndustryId = 180 -- صندوق
	and AssetClassId = 3 -- طلا 
	and InstrumentId > 0 -- ETF
	and Instrument not like '%اختیار%'
	and Instrument not like '%شمش%'
	and MarketId = 6 -- بازار معاملات اصلی