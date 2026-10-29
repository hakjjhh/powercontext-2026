from powercontext.sources.pdf_pages import _toc_entries, _toc_section_ranges


def test_toc_section_ranges_map_printed_pages_to_physical_pages() -> None:
    pages = [
        (1, "Cover page"),
        (
            2,
            "INDEX PART I "
            "Item 1. Business 1 "
            "Item 1A. Risk Factors 3 "
            "Item 2. Properties 5 "
            "Item 3. Legal Proceedings 5 "
            "Item 7. Management Discussion 7 "
            "Item 8. Financial Statements 9",
        ),
        (3, "Item 1. Business\nProducts and customers"),
        (4, "Business continued"),
        (5, "Item 1A. Risk Factors\nRisk discussion"),
        (6, "Risk discussion continued"),
        (7, "Item 2. Properties\nItem 3. Legal Proceedings"),
        (8, "Properties and legal continued"),
        (9, "Item 7. Management Discussion\nResults of operations"),
        (10, "Results continued"),
        (11, "Item 8. Financial Statements\nConsolidated statements"),
    ] + [(page, f"Financial statement page {page}") for page in range(12, 21)]

    entries, toc_pages = _toc_entries(pages)
    ranges = _toc_section_ranges(pages)

    assert toc_pages == frozenset({2})
    assert entries["1"] == 1
    assert entries["8"] == 9
    assert ranges["1"] == (3, 4)
    assert ranges["1a"] == (5, 6)
    assert ranges["2"] == (7, 8)
    assert ranges["3"] == (7, 8)
    assert ranges["7"] == (9, 10)
    assert ranges["8"] == (11, 20)


def test_toc_entries_accept_page_numbers_glued_to_title_text() -> None:
    pages = [
        (1, "Cover page"),
        (
            2,
            "INDEX PART I "
            "Item 1. Business 3 "
            "Item 1A. Risk Factors 6 "
            "Item 2. Properties 16 "
            "Item 3. Legal Proceedings 16 "
            "Item 7. Management Discussion 19 "
            "Item 16. Form 10-K Summary 76",
        ),
    ]

    entries, _toc_pages = _toc_entries(pages)

    assert entries["1"] == 3
    assert entries["7"] == 19
    assert entries["16"] == 76