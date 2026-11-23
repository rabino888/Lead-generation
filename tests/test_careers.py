"""Careers discovery ranking and +1 portal hop."""
from __future__ import annotations

from agent.integrations.careers import (
    ats_board_identity,
    canonical_ats_board_url,
    careers_index_needs_portal_hop,
    extract_careers_links,
    fetch_ats_open_roles,
    open_roles_from_ats,
    pick_careers_portal_hop,
    preferred_ats_board_url,
    titles_from_ats_payload,
    titles_from_icims_html,
)


def test_extract_prefers_ats_over_marketing():
    md = """
    Join us at [Careers](https://acme.com/careers).
    Or apply via https://jobs.ashbyhq.com/acme
    """
    links = extract_careers_links(md, "https://acme.com")
    assert links[0].startswith("https://jobs.ashbyhq.com/acme")


def test_extract_prefers_yc_jobs_over_hash_careers():
    md = """
    [Careers](https://www.ycombinator.com/companies/silimate/jobs)
    See also https://www.silimate.com/company#careers
    """
    links = extract_careers_links(md, "https://www.silimate.com")
    assert "ycombinator.com" in links[0]


def test_rejects_docs_api_jobs_urls():
    md = """
    API jobs: https://docs.fiddler.ai/sdk-api/rest-api/jobs
    Real board: https://jobs.ashbyhq.com/fiddler
    """
    links = extract_careers_links(md, "https://www.fiddler.ai")
    assert links
    assert all("docs.fiddler" not in u for u in links)
    assert "ashbyhq.com" in links[0]


def test_needs_hop_on_marketing_cta_page():
    md = """
    # Careers at Acme
    We're hiring talented people. [See open roles](https://jobs.ashbyhq.com/acme)
    Join our team and help build the future.
    """
    assert careers_index_needs_portal_hop(md, careers_url="https://acme.com/careers")


def test_no_hop_when_already_on_ats():
    md = "Senior Engineer\nStaff Scientist\nApply for Platform Engineer"
    assert not careers_index_needs_portal_hop(
        md, careers_url="https://jobs.ashbyhq.com/acme"
    )


def test_pick_hop_from_cta_anchor():
    md = """
    # Careers
    We're growing. [See open roles](https://job-boards.greenhouse.io/acme)
    """
    hop = pick_careers_portal_hop(
        md, "https://acme.com", current_url="https://acme.com/careers"
    )
    assert hop == "https://job-boards.greenhouse.io/acme"


def test_pick_hop_from_inline_ats_url():
    md = """
    Open roles live at https://jobs.ashbyhq.com/plenful?utm_source=site
    """
    hop = pick_careers_portal_hop(
        md, "https://plenful.com", current_url="https://plenful.com/careers"
    )
    assert hop and "ashbyhq.com/plenful" in hop


def test_ashby_board_identity_strips_job_id_and_utm():
    ident = ats_board_identity(
        "https://jobs.ashbyhq.com/ivo-inc/5c151210-be10-452a-9ba3-4fc92eb68c2c?utm_source=site"
    )
    assert ident == ("ashby", "ivo-inc")
    assert canonical_ats_board_url(
        "https://jobs.ashbyhq.com/confido?utm_source=EpLvqzJP3X"
    ) == "https://jobs.ashbyhq.com/confido"


def test_greenhouse_board_identity_from_single_posting():
    ident = ats_board_identity(
        "https://job-boards.greenhouse.io/scoutai/jobs/4643324008"
    )
    assert ident == ("greenhouse", "scoutai")


def test_titles_from_ashby_payload_skips_unlisted_and_dupes():
    titles = titles_from_ats_payload(
        "ashby",
        {
            "jobs": [
                {"title": "Senior Software Engineer", "isListed": True},
                {"title": "Secret Role", "isListed": False},
                {"title": "Senior Software Engineer", "isListed": True},
                {"title": "Careers", "isListed": True},
            ]
        },
    )
    assert titles == ["Senior Software Engineer"]


def test_fetch_ats_open_roles_uses_board_api(monkeypatch):
    seen: list[str] = []

    class Resp:
        status_code = 200

        def json(self):
            return {"jobs": [{"title": "Product Engineer", "isListed": True}]}

    def fake_get(url, **kwargs):
        seen.append(url)
        return Resp()

    monkeypatch.setattr("agent.integrations.careers.httpx.get", fake_get)
    got = open_roles_from_ats("https://jobs.ashbyhq.com/Arlo/7e420c5d-e49e-423d-97e4-b763abb3e0cd")
    assert got is not None
    assert got["careers_url"] == "https://jobs.ashbyhq.com/Arlo"
    assert got["open_roles"] == ["Product Engineer"]
    assert seen == ["https://api.ashbyhq.com/posting-api/job-board/Arlo"]


def test_titles_from_icims_job_urls():
    html = """
    <a href="https://careers-lrri.icims.com/jobs/2441/accounts-payable-specialist/job?in_iframe=1">
    <a href="https://careers-lrri.icims.com/jobs/2526/laboratory-animal-sciences-technician-ii/job">
    <a href="https://careers-lrri.icims.com/jobs/search?ss=1">
    """
    assert titles_from_icims_html(html) == [
        "Accounts Payable Specialist",
        "Laboratory Animal Sciences Technician II",
    ]


def test_preferred_ats_board_from_marketing_page():
    md = """
    [Careers](https://cinder.ai/careers)
    [See open roles](https://jobs.ashbyhq.com/cinder)
    """
    assert preferred_ats_board_url(md, "https://cinder.ai") == "https://jobs.ashbyhq.com/cinder"


def test_fetch_ats_open_roles_ignores_docs_url(monkeypatch):
    def fake_get(url, **kwargs):
        raise AssertionError("should not fetch")

    monkeypatch.setattr("agent.integrations.careers.httpx.get", fake_get)
    assert fetch_ats_open_roles("https://docs.fiddler.ai/sdk-api/rest-api/jobs") == []


def test_pick_hop_skips_current_url():
    md = "Apply at https://acme.com/careers — we're hiring!"
    hop = pick_careers_portal_hop(
        md, "https://acme.com", current_url="https://acme.com/careers"
    )
    assert hop is None


def test_jobs_co_uk_is_not_the_company_portal():
    from agent.integrations.careers import (
        extract_careers_links,
        is_generic_job_board,
        without_generic_job_boards,
    )

    markdown = (
        "### [https://www.allianz.co.uk/]\n"
        "Allianz insurance for home and motor.\n"
        "\n---\n\n"
        "### [https://jobs.co.uk/]\n"
        "UK Job Search. Register and upload your CV.\n"
    )
    assert is_generic_job_board("https://www.jobs.co.uk/search")
    assert extract_careers_links(markdown, "https://allianz.co.uk") == []
    kept = without_generic_job_boards(markdown)
    assert "jobs.co.uk" not in kept
    assert "Allianz insurance" in kept


def test_footer_label_can_say_careers_at_the_company():
    from agent.integrations.careers import footer_careers_links

    html = """
    <a href="https://www.simplybusiness.co.uk/about-us/careers/">Careers at Simply Business</a>
    <a href="https://travelers.wd5.myworkdayjobs.com/SimplyBusinesssExternal">Current opportunities</a>
    <a href="https://www.hsbc.co.uk/help/life-events/unexpected-change/redundancy/">Losing your job</a>
    <a href="https://jobs.co.uk/">Jobs</a>
    """
    links = footer_careers_links(html, "https://www.simplybusiness.co.uk/")
    assert links[0].startswith("https://travelers.wd5.myworkdayjobs.com/")
    assert "https://www.simplybusiness.co.uk/about-us/careers/" in links
    assert all("jobs.co.uk" not in url for url in links)
    assert all("redundancy" not in url for url in links)
    from agent.integrations.careers import _probe_candidates, _registered_domain

    assert _registered_domain("https://www.aviva.co.uk/") == "aviva.co.uk"
    assert _registered_domain("https://admiral.com") == "admiral.com"
    assert "https://jobs.co.uk" not in _probe_candidates("https://aviva.co.uk")
    assert any(
        "jobsportal.lv.com" in url for url in _probe_candidates("https://lv.com")
    )


def test_footer_jobs_link_on_a_sibling_domain():
    from agent.integrations.careers import footer_careers_links

    html = """
    <footer>
      <a href="https://admiraljobs.co.uk/">Jobs</a>
      <a href="https://www.facebook.com/admiral">Facebook</a>
    </footer>
    """
    assert footer_careers_links(html, "https://www.admiral.com/") == [
        "https://admiraljobs.co.uk/"
    ]


def test_footer_careers_ignores_generic_job_board():
    from agent.integrations.careers import find_careers_page, footer_careers_links

    html = """
    <a href="https://jobs.co.uk/">Jobs</a>
    <a href="https://careers.aviva.co.uk/">Careers</a>
    """
    links = footer_careers_links(html, "https://www.aviva.co.uk/")
    assert links == ["https://careers.aviva.co.uk/"]

    def fake_fetch(url, timeout=12):
        return html

    import agent.integrations.careers as careers

    original = careers._fetch_html
    careers._fetch_html = fake_fetch
    try:
        assert find_careers_page("https://www.aviva.co.uk/") == "https://careers.aviva.co.uk"
    finally:
        careers._fetch_html = original


def test_sitemap_article_about_leaving_a_job_is_not_a_careers_page():
    from agent.integrations.careers import filter_careers_urls

    urls = [
        "https://www.aviva.co.uk/retirement/pension-basics/what-happens-to-your-pension-when-you-leave-a-job/",
        "https://www.admiral.com/magazine/guides/motor/diy-jobs-to-help-your-car-pass-its-mot",
    ]
    assert filter_careers_urls(urls, "https://aviva.co.uk") == []
    assert filter_careers_urls(urls, "https://admiral.com") == []


def test_eploy_headings_and_attrax_tiles_and_job_sitemap():
    from agent.integrations.careers import titles_from_job_sitemap, titles_from_vacancy_html

    eploy = """
    <h2>Log in to your account</h2>
    <h1>11 Vacancies</h1>
    <h2>Cloud Operations Lead</h2>
    <h2>IT Audit Manager</h2>
    """
    assert titles_from_vacancy_html(eploy) == [
        "Cloud Operations Lead",
        "IT Audit Manager",
    ]
    attrax = """
    <a class="attrax-vacancy-tile__title" href="/job/reserving-actuary-in-cardiff-jid-3564">
      Reserving Actuary
    </a>
    """
    assert titles_from_vacancy_html(attrax) == ["Reserving Actuary"]
    sitemap = """
    <url><loc>https://www.admiraljobs.co.uk/job/senior-reserving-actuary-in-cardiff-jid-3454</loc></url>
    """
    assert titles_from_job_sitemap(sitemap) == ["Senior Reserving Actuary"]
    marketing = """
    <h2>About AXA</h2>
    <h2>Your Benefits</h2>
    <p>See our vacancies and join the team.</p>
    """
    assert titles_from_vacancy_html(marketing) == []


def test_aviva_talent_project_slugs_and_see_all_roles_button():
    from agent.integrations.careers import titles_from_vacancy_html

    page = """
    <div class="js-latestVacancies_container">
      <a target="_blank" rel="noopener noreferrer"
         href="https://aviva.talent.community.com/projects/inbound-customer-advisor/?source=Aviva%20Rec&amp;utm_source=careersSite&amp;utm_medium=referral">
        <div class="m-card-content_inner"></div>
      </a>
      <a href="https://aviva.talent.community.com/projects/roadside-technician-green/?source=Aviva%20Rec">
        <div class="m-card-content"></div>
      </a>
      <a href="https://aviva.talent.community.com/projects/senior-category-manager/?source=x"></a>
      <a href="https://aviva.talent.community.com/projects/senior-test-manager/7726?source=x"></a>
      <a href="https://aviva.talent.community.com/projects/loss-adjuster/7752570?dist=&amp;source=Aviva"></a>
      <a href="https://aviva.talent.community.com/projects/home-desk-consultant/7751?source=x"></a>
    </div>
    <a href="https://careers.aviva.co.uk/apply/" class="a-button a-button--primary">See all 151 roles</a>
    <h2>Our vacancies</h2>
    """
    assert titles_from_vacancy_html(page) == [
        "Inbound Customer Advisor",
        "Roadside Technician Green",
        "Senior Category Manager",
        "Senior Test Manager",
        "Loss Adjuster",
        "Home Desk Consultant",
    ]


def test_project_slug_drops_trailing_numeric_id_inside_open_roles():
    from agent.integrations.careers import titles_from_vacancy_html

    page = """
    <div id="open_roles">
      <a href="/projects/loss-adjuster-7752570?source=x">Learn more</a>
    </div>
    <h2 class="job-title">Home Desk Consultant</h2>
    """
    assert titles_from_vacancy_html(page) == [
        "Loss Adjuster",
        "Home Desk Consultant",
    ]


def test_animal_friends_vacancy_card_heading_without_role_dictionary():
    from agent.integrations.careers import titles_from_vacancy_html

    page = """
    <h2>About AXA</h2>
    <div class="block currentvacanciesblock">
      <h2>Our vacancies</h2>
      <div id="vacancies-filters"></div>
      <div id="vacancies-content" class="vacancy-card-container">
        <div class="vacancy card" data-department="HR">
          <div class="card-body">
            <div class="department-pill desktop">HR</div>
            <h1>Reward Partner - Europe</h1>
            <p class="card-text">London</p>
          </div>
        </div>
        <div class="vacancy card" data-department="Risk">
          <div class="department-pill">Risk</div>
          <h1>Motor Pricing</h1>
        </div>
      </div>
    </div>
    <a href="/careers/all">See all 151 roles</a>
    """
    assert titles_from_vacancy_html(page) == [
        "Reward Partner - Europe",
        "Motor Pricing",
    ]


def test_listing_cta_phrases_and_jobs_co_uk_is_not_followed():
    from agent.integrations.careers import listing_cta_url

    phrases = [
        "See all 151 roles",
        "See all roles",
        "Check out open positions",
        "View open roles",
        "Current opportunities",
        "See open roles",
        "Browse vacancies",
    ]
    for phrase in phrases:
        html = f'<a href="https://careers.example.com/apply/">{phrase}</a>'
        assert listing_cta_url(html, "https://example.com/careers") == (
            "https://careers.example.com/apply/"
        )
    blocked = '<a href="https://www.jobs.co.uk/search">See all roles</a>'
    assert listing_cta_url(blocked, "https://www.aviva.co.uk/") is None


def test_one_listing_cta_hop_reads_the_destination(monkeypatch):
    from agent.integrations.careers import open_roles_from_careers_page

    pages = {
        "https://careers.example.com": (
            '<a href="https://careers.example.com/apply/">See all 151 roles</a>'
        ),
        "https://careers.example.com/apply": (
            '<div class="vacancy card"><h1>Motor Pricing</h1></div>'
            '<a href="https://careers.example.com/all">Browse vacancies</a>'
        ),
        "https://careers.example.com/all": (
            '<div class="vacancy card"><h1>Should Not Appear</h1></div>'
        ),
    }
    seen: list[str] = []

    def fake(url, timeout=12):
        key = url.split("?")[0].rstrip("/")
        seen.append(key)
        return (200, pages[key]) if key in pages else (404, "")

    monkeypatch.setattr("agent.integrations.careers._fetch_response", fake)
    got = open_roles_from_careers_page("https://careers.example.com/")
    assert got is not None
    assert got["open_roles"] == ["Motor Pricing"]
    assert got["careers_url"] == "https://careers.example.com/apply"
    assert "https://careers.example.com/all" not in seen


def test_listing_cta_does_not_hop_twice(monkeypatch):
    from agent.integrations.careers import open_roles_from_careers_page

    pages = {
        "https://careers.example.com": (
            '<a href="https://jobs.example.com/roles">View open roles</a>'
        ),
        "https://jobs.example.com/roles": (
            '<a href="https://jobs.example.com/all">See all roles</a>'
        ),
        "https://jobs.example.com/all": (
            '<div class="vacancy card"><h1>Motor Pricing</h1></div>'
        ),
    }
    seen: list[str] = []

    def fake(url, timeout=12):
        key = url.split("?")[0].rstrip("/")
        seen.append(key)
        return (200, pages[key]) if key in pages else (404, "")

    monkeypatch.setattr("agent.integrations.careers._fetch_response", fake)
    assert open_roles_from_careers_page("https://careers.example.com") is None
    assert "https://jobs.example.com/all" not in seen


def test_jobs_co_uk_cta_and_page_are_not_fetched(monkeypatch):
    from agent.integrations.careers import open_roles_from_careers_page

    seen: list[str] = []

    def fake(url, timeout=12):
        seen.append(url)
        if "jobs.co.uk" in url:
            raise AssertionError(url)
        return 200, '<a href="https://jobs.co.uk/">See all roles</a>'

    monkeypatch.setattr("agent.integrations.careers._fetch_response", fake)
    assert open_roles_from_careers_page("https://careers.aviva.co.uk/") is None
    assert open_roles_from_careers_page("https://www.jobs.co.uk/search") is None
    assert all("jobs.co.uk" not in url for url in seen)


def test_embedded_ashby_board_is_read_without_a_second_hop(monkeypatch):
    from agent.integrations.careers import open_roles_from_careers_page

    def fake_fetch(url, timeout=12):
        key = url.split("?")[0].rstrip("/")
        if key == "https://acme.com/jobs":
            return 200, (
                '<a href="https://jobs.ashbyhq.com/acme/embed?version=2"></a>'
                '<a href="https://jobs.example.com/all">See all roles</a>'
            )
        return 404, ""

    seen: list[str] = []

    class Resp:
        status_code = 200

        def json(self):
            return {"jobs": [{"title": "Claims Handler", "isListed": True}]}

    def fake_get(url, **kwargs):
        seen.append(url)
        return Resp()

    monkeypatch.setattr("agent.integrations.careers._fetch_response", fake_fetch)
    monkeypatch.setattr("agent.integrations.careers.httpx.get", fake_get)
    got = open_roles_from_careers_page("https://acme.com/jobs")
    assert got is not None
    assert got["open_roles"] == ["Claims Handler"]
    assert got["careers_url"] == "https://jobs.ashbyhq.com/acme"
    assert seen == ["https://api.ashbyhq.com/posting-api/job-board/acme"]
    assert all("jobs.example.com" not in url for url in seen)


def test_embedded_board_skips_icims_cookie_and_privacy_pages():
    from agent.integrations.careers import _embedded_board_url

    noise = (
        "https://cookie-policy-scripts.icims.com/axa/careers.axa.co.uk/script.js "
        "https://www.icims.com/legal/privacy-notice-website/ "
        "https://jobs.ashbyhq.com/flock/embed?version=2"
    )
    assert _embedded_board_url(noise) == "https://jobs.ashbyhq.com/flock/embed?version=2"
    board = (
        "https://www.icims.com/legal/privacy-notice-website/ "
        "https://careers-uknordicswitz-berkley.icims.com/jobs/search?ss=1"
    )
    assert (
        _embedded_board_url(board)
        == "https://careers-uknordicswitz-berkley.icims.com/jobs/search?ss=1"
    )
    axa = (
        "https://cookie-policy-scripts.icims.com/axa/script.js "
        "https://move-en-axa.icims.com/jobs/login?loginOnly=1 "
        "https://careers-en-axa.icims.com/jobs/login?loginOnly=1&redirect=dashboard"
    )
    assert _embedded_board_url(axa).startswith(
        "https://careers-en-axa.icims.com/jobs/login"
    )


def test_handlebars_vacancy_shell_is_js_only_and_not_a_title(monkeypatch):
    from agent.integrations.careers import open_roles_report, titles_from_vacancy_html

    page = """
    <h2>Our vacancies</h2>
    <div id="vacancies-content" class="vacancy-card-container"></div>
    <div id="vacancies-message-content">Please wait while we load our current vacancies.</div>
    <script id="vacancies-template" type="text/x-handlebars-template">
      <div class="vacancy card" data-department="{{this.department}}">
        <h1>{{this.title}}</h1>
      </div>
    </script>
    <script>var vacancies = await window.RetrieveVacancies('problem', 'none');</script>
    """
    assert titles_from_vacancy_html(page) == []

    def fake_fetch(url, timeout=12):
        if "vacanciessitemap" in url:
            return 404, ""
        return 200, page

    monkeypatch.setattr("agent.integrations.careers._fetch_response", fake_fetch)
    report = open_roles_report("https://www.animalfriends.co.uk/careers")
    assert report["open_roles"] == []
    assert report["reason"] == "js_only"


def test_workday_shell_is_js_only(monkeypatch):
    from agent.integrations.careers import open_roles_report

    def fake_fetch(url, timeout=12):
        if "vacanciessitemap" in url:
            return 404, ""
        return 200, "<html><body><div id='root'>Careers</div><script src='/app.js'></script></body></html>"

    monkeypatch.setattr("agent.integrations.careers._fetch_response", fake_fetch)
    report = open_roles_report("https://example.wd5.myworkdayjobs.com/Careers")
    assert report["open_roles"] == []
    assert report["reason"] == "js_only"


def test_markdown_hop_recognizes_see_all_counted_roles():
    hop = pick_careers_portal_hop(
        "[See all 151 roles](https://careers.aviva.co.uk/apply/)",
        "https://www.aviva.co.uk/",
        current_url="https://careers.aviva.co.uk",
    )
    assert hop == "https://careers.aviva.co.uk/apply/"
