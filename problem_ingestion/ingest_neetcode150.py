import uuid
import json
import logging
import re
from tqdm import tqdm
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE_URL = 'https://neetcode.io'

# --- Setup logging ---
logging.basicConfig(
    filename='neetcode_scrape.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

def parse_neetcode_links_html(file_path):
    """Parse the neetcode150_links.html and return a list of card dicts with neetcode/leetcode links."""
    from bs4 import BeautifulSoup
    with open(file_path, "r", encoding="utf-8") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")
    results = []

    tbody = soup.find("tbody")
    if not tbody:
        raise RuntimeError("Could not find tbody element.")

    for tr in tbody.find_all("tr"):
        # Find title/links
        title_td = tr.find("td", style=lambda x: x and "width: 350px" in x)
        if not title_td:
            continue

        main_a = title_td.find("a", href=True)
        if not main_a:
            continue
        title = main_a.get_text(strip=True)
        rel_link = main_a["href"]
        neetcode_link = BASE_URL + rel_link

        ext_a = title_td.find("a", target="_blank")
        ext_link = ext_a["href"] if ext_a else ""

        diff_td = tr.find("td", class_="diff-col")
        difficulty = ""
        if diff_td:
            diff_btn = diff_td.find("b")
            difficulty = diff_btn.get_text(strip=True) if diff_btn else ""

        results.append({
            "uuid": str(uuid.uuid4()),
            "card_name": title,
            "description": "",
            "python_code": "",
            "dsa": "",
            "time_complexity": "",
            "space_complexity": "",
            "difficulty": difficulty,
            "leetcode_link": ext_link,
            "neetcode_link": neetcode_link
        })
    return results

def clean_dsa(text):
    return re.sub(r'^\d+\.\s*', '', text).strip()

def extract_big_o(text):
    m = re.search(r'O\([^\)]+\)', text)
    return m.group(0) if m else ""

def extract_question_tab_html(page):
    tab = page.locator("span.tab-header", has_text="Question")
    tab.wait_for(state="visible", timeout=15000)
    tab.click()
    page.wait_for_selector("div.my-article-component-container", timeout=15000)
    return page.content()

def extract_solution_tab_html(page):
    tab = page.locator("span.tab-header", has_text="Solution")
    tab.wait_for(state="visible", timeout=15000)
    tab.click()
    page.wait_for_selector("div.my-article-component-container h2", timeout=15000)
    return page.content()

def clean_example_pre_block(pre_tag):
    """
    Clean text inside a <pre> so it becomes a clean single line:
    Input: nums = [1,2,3,3]
    Output: true
    (handles spaces and linebreaks correctly)
    """
    example = pre_tag.get_text('\n', strip=False)
    # Collapse [\n1\n,\n2\n...] into [1,2,3,3]
    # Remove all newlines and spaces *immediately after or before bracket/comma/colon/operators*
    example = re.sub(r'\s*\n\s*', '', example)         # remove all newlines surrounded by spaces
    example = re.sub(r'\s{2,}', ' ', example)          # collapse multiple spaces
    example = re.sub(r'(\[)\s+', r'\1', example)       # trim spaces after [
    example = re.sub(r'\s+(\])', r'\1', example)       # trim spaces before ]
    example = re.sub(r',\s+', ',', example)            # trim spaces after comma
    example = re.sub(r'\s+(\,)', r'\1', example)       # trim spaces before comma
    example = re.sub(r':\s+', ': ', example)           # ensure only one space after colon
    example = example.strip()
    return example

def clean_description_block(div_block):
    """
    Render the textual description, joining paragraphs with double newlines,
    but flattening <pre> code examples so Input/Output are on a single line.
    """
    out_lines = []
    for child in div_block.children:
        if getattr(child, "name", None) == "p":
            out_lines.append(child.get_text(" ", strip=True))
        elif getattr(child, "name", None) == "pre":
            # CLEANED
            code = clean_example_pre_block(child)
            out_lines.append(code)
        elif getattr(child, "name", None) == "div":
            pre = child.find("pre")
            if pre:
                code = clean_example_pre_block(pre)
                out_lines.append(code)
            else:
                for sub in child.children:
                    if getattr(sub, "name", None) == "p":
                        out_lines.append(sub.get_text(" ", strip=True))
        elif isinstance(child, str):
            t = child.strip()
            if t:
                out_lines.append(t)
    # Join with double newlines between blocks (as before)
    return "\n\n".join([line for line in out_lines if line.strip()]).strip()

def parse_problem_page_playwright(page, neetcode_url):
    result = {
        'description': '',
        'dsa': '',
        'python_code': '',
        'time_complexity': '',
        'space_complexity': ''
    }

    # Description from the first div inside .my-article-component-container in Question tab
    try:
        q_html = extract_question_tab_html(page)
        q_soup = BeautifulSoup(q_html, "html.parser")
        container = q_soup.find('div', class_='my-article-component-container')
        # ---- THE KEY FIX: get ONLY the first <div> direct child ----
        main_block = None
        if container:
            for c in container.children:
                if getattr(c, "name", None) == "div":
                    main_block = c
                    break
        if not main_block:
            raise ValueError("Did not find first <div> inside question container")

        # Get text cleanly as displayed (paragraphs and examples with spacing)
        result['description'] = clean_description_block(main_block)
    except Exception as e:
        logging.error(f"Description parse error at {neetcode_url}: {e}")

    # Solution tab: dsa/code/complexities
    try:
        s_html = extract_solution_tab_html(page)
        soup = BeautifulSoup(s_html, "html.parser")

        sol_divs = soup.find_all("div", class_="my-article-component-container")
        sol_div = sol_divs[-1] if sol_divs else None
        if not sol_div:
            raise ValueError("No solution container found")

        h2s = sol_div.find_all("h2")
        if h2s:
            result['dsa'] = clean_dsa(h2s[-1].get_text(strip=True))

        # ------ KEY FIX: python code ------
        # Find the first <pre class=language-python>, then get its <code> tag's .text as code
        python_code = ""
        for sib in h2s[-1].find_all_next():
            if sib.name == "pre" and "language-python" in (sib.get("class") or []):
                code_tag = sib.find("code")
                if code_tag:
                    python_code = code_tag.text.strip('\n')
                else:
                    # fallback
                    python_code = sib.get_text('\n', strip=False).strip('\n')
                break
            if sib.name == "h2":
                break
        result['python_code'] = python_code

        # Time/space complexity
        tc, sc = "", ""
        for sib in h2s[-1].find_all_next():
            if sib.name == "li":
                txt = sib.get_text(" ", strip=True)
                if not tc and "time complexity" in txt.lower():
                    tc = extract_big_o(txt)
                elif not sc and "space complexity" in txt.lower():
                    sc = extract_big_o(txt)
                if tc and sc:
                    break
            if sib.name == "h2":
                break
        result['time_complexity'] = tc
        result['space_complexity'] = sc

    except Exception as e:
        logging.error(f"Solution parse error at {neetcode_url}: {e}")

    return result

def main():
    cards = parse_neetcode_links_html("neetcode150_links.html")
    output = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=75)  # show browser, slow enough to watch, close after each card
        context = browser.new_context(viewport={'width':1400, 'height':900})
        page = context.new_page()

        for card in tqdm(cards, desc="Scraping Neetcode", ncols=90):
            url = card['neetcode_link']
            try:
                page.goto(url, timeout=45000)
                extracted = parse_problem_page_playwright(page, url)
                card.update(extracted)
            except Exception as e:
                logging.error(f"ERROR scraping page {url}: {repr(e)}")
            output.append(card)
            # Human: press Enter to continue to next card (optional)
            # input("Press [Enter] for next problem...")    # Uncomment to control manually
        browser.close()

    with open("neetcode150_scraped.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(output)} problems to neetcode150_scraped.json")

if __name__ == "__main__":
    main()
