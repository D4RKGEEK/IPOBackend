from app.clients import BSEClient, NSEClient, SEBIClient
from app.utils import clean_url, is_zip_url, normalize_company_name


def test_parse_sebi_listing_extracts_detail_and_abridged_pdf():
    html = """
    <input name="totalpage" value="86" />
    <p>1 to 25 of 2127 records</p>
    <table>
      <tr>
        <td>Apr 07, 2026</td>
        <td>
          <a href="https://www.sebi.gov.in/filings/public-issues/apr-2026/rentomojo-limited-drhp_100746.html"
             title="Rentomojo Limited - DRHP <br><a href='https://www.sebi.gov.in/sebi_data/commondocs/apr-2026/Rentomojo%20Limited-Draft%20Abridged%20Prospectus_p.pdf'>Draft</a>">
            Rentomojo Limited - DRHP
          </a>
        </td>
      </tr>
    </table>
    """
    parsed = SEBIClient(client=None).parse_listing(html, "DRHP")

    assert parsed["total_pages"] == 86
    assert parsed["total_records"] == 2127
    assert parsed["records"][0].company_name == "Rentomojo Limited"
    assert parsed["records"][0].filing_date == "2026-04-07"
    assert parsed["records"][0].document_urls.abridged_prospectus_pdf.endswith("_p.pdf")


def test_parse_sebi_detail_extracts_iframe_pdf():
    html = """
    <iframe src="../../web/?file=https://www.sebi.gov.in/sebi_data/attachdocs/apr-2026/1775525404083_1204.pdf"></iframe>
    """
    parsed = SEBIClient(client=None).parse_detail_page(html)

    assert parsed["pdf_url"] == "https://www.sebi.gov.in/sebi_data/attachdocs/apr-2026/1775525404083_1204.pdf"


def test_parse_bse_ipos_maps_status():
    parsed = BSEClient(client=None).parse_ipos(
        {
            "Table": [
                {
                    "Scrip_cd": 4560,
                    "Scrip_Name": "Hexagon Nutrition Limited",
                    "Start_Dt": "2026-06-05T00:00:00",
                    "End_Dt": "2026-06-09T00:00:00",
                    "Price_Band": "42.00 - 45.00",
                    "Face_Val": 10.0,
                    "IR_flag": "IPO",
                    "Status": "F",
                    "eXCHANGE_PLATFORM": "MainBoard",
                    "FLAG": 7,
                    "IPO_NO": 7720,
                }
            ]
        }
    )

    assert parsed[0].status == "upcoming"
    assert parsed[0].platform == "MainBoard"
    assert parsed[0].start_date == "2026-06-05"


def test_normalize_company_name():
    assert normalize_company_name("Rentomojo Limited - DRHP") == "RENTOMOJO LTD"


def test_nse_parse_response():
    """Test NSE offer-docs response parsing with clean URL handling."""
    raw_data = [
        {
            "company": "Rentomojo Limited",
            "symbol": "-",
            "drhp": "Draft Prospectus/Draft Red Herring Prospectus",
            "drhpDate": "26-May-2026",
            "drhpAttach": "https://nsearchives.nseindia.com/corporate/Rentomojo.zip",
            "drhpAttachFileSize": "9.07 MB",
            "drhpStatus": "Under Process",
            "fpAttach": "https://nsearchives.nseindia.com/corporate/FP_Rentomojo.pdf",
        },
        {
            "company": "Arohan Financial Services Limited",
            "drhpAttach": "https://nsearchives.nseindia.com/corporate/d_Arohan.zip\r",
            "drhpAttachFileSize": None,
        },
    ]
    parsed = NSEClient(client=None)._parse_response(raw_data, index="equities")

    # First record: Rentomojo
    assert parsed[0].company_name == "Rentomojo Limited"
    assert parsed[0].drhp_attach is not None
    assert parsed[0].drhp_attach.url == "https://nsearchives.nseindia.com/corporate/Rentomojo.zip"
    assert parsed[0].drhp_attach.is_zip is True
    assert parsed[0].drhp_attach.file_size == "9.07 MB"
    assert parsed[0].drhp_status == "Under Process"
    assert parsed[0].fp_attach is not None
    assert parsed[0].fp_attach.is_zip is False  # .pdf not .zip

    # Second record: Arohan with trailing \r — should be cleaned
    assert parsed[1].company_name == "Arohan Financial Services Limited"
    assert parsed[1].drhp_attach is not None
    assert parsed[1].drhp_attach.url == "https://nsearchives.nseindia.com/corporate/d_Arohan.zip"
    assert parsed[1].drhp_attach.is_zip is True


def test_clean_url_strips_trailing_cr():
    assert clean_url("https://example.com/file.zip\r") == "https://example.com/file.zip"
    assert clean_url("https://example.com/file.pdf ") == "https://example.com/file.pdf"
    assert clean_url("  https://example.com/file  ") == "https://example.com/file"
    assert clean_url(None) is None
    assert clean_url("") is None


def test_is_zip_url():
    assert is_zip_url("https://example.com/file.zip") is True
    assert is_zip_url("https://example.com/file.ZIP") is True
    assert is_zip_url("https://example.com/file.pdf") is False
    assert is_zip_url(None) is False
    assert is_zip_url("") is False
