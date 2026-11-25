"""Place spellings paired by the same episode in pinned source passages."""

ATTESTED_PLACES = (
    # Both accounts describe Li Shidao learning that the same fort had fallen.
    (
        dict(place='凌雲柵', reference=dict(
            page_id=61172, book_id='KR2a0026', revision_id=2348866,
            source_sha256='9511be42e01fff892eecaa384b53d28ed254f1015d37dfacb7c2d7be3fab71aa',
            title='舊唐書/卷15', heading='元和十一年',
            source_url='https://zh.wikisource.org/w/index.php?oldid=2348866',
            ranges=[[9, 1147, 1168]], quote='鄆州李師道加檢校司空。師道聞拔凌雲柵，乃懼',
        )),
        dict(place='淩雲柵', reference=dict(
            page_id=100439, book_id='KR2a0027', revision_id=2693539,
            source_sha256='65220ce1766ab8742cceb571f6dce779ae00d9a7d94079dab454b011e9ddfd22',
            title='新唐書/卷213', heading='李正己',
            source_url='https://zh.wikisource.org/w/index.php?oldid=2693539',
            ranges=[[2, 2056, 2069]], quote='及聞李光顏拔淩雲柵，始大懼',
        )),
    ),
    # The biographies record Cao Cao overtaking Liu Bei at Dangyang.
    (
        dict(place='長坂', reference=dict(
            page_id=12873, book_id='KR2a0012', revision_id=2583378,
            source_sha256='769cb60d6e8a361571ac998da01ee24ff1c768f8b8eb8bdb10b569af3a7ae332',
            title='三國志/卷32', heading='先主 劉備',
            source_url='https://zh.wikisource.org/w/index.php?oldid=2583378',
            ranges=[[47, 32, 67]], quote='聞先主已過，曹公將精騎五千急追之，一日一夜行三百餘里，及於當陽之長坂。',
        )),
        dict(place='長阪', reference=dict(
            page_id=257, book_id='KR2a0012', revision_id=2583383,
            source_sha256='b0afa89b0104a4b021520fc247bc3e632ca8ad3452f6b2b94be8b25540757b2e',
            title='三國志/卷36', heading='張飛',
            source_url='https://zh.wikisource.org/w/index.php?oldid=2583383',
            ranges=[[22, 84, 102]], quote='曹公追之，一日一夜，及於當陽之長阪。',
        )),
    ),
)
