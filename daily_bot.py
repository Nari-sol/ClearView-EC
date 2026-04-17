import requests
from bs4 import BeautifulSoup
import re
import time
from datetime import datetime
import random
import os
import unicodedata
import json

# 検索ターゲット（カテゴリ・品番）および監視対象ショップは実行時に GAS から取得します
SECRET_TOKEN = "COMMANDER_SECRET_2026"
GAS_WEBAPP_URL = os.environ.get("GAS_WEBAPP_URL")

# --- Helpers ---
def normalize_text(text):
    if not text: return ""
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).lower()

def save_debug_html(html_content, filename="debug_page.html"):
    """診断用にHTMLをローカルファイルに保存する"""
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"      [Diagnostic] HTML saved to {filename}")
    except Exception as e:
        print(f"      [Diagnostic] Failed to save HTML: {e}")

def fetch_benchmark_data(keyword, target_shops, max_pages=3):
    """
    Yahoo!ショッピングを複数ページ巡回し、指定したベンチマーク店舗の出品状況を取得する
    """
    results = {} 
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://shopping.yahoo.co.jp/",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1"
    }

    url = "https://shopping.yahoo.co.jp/search"
    
    for page in range(1, max_pages + 1):
        if len(results) >= len(target_shops):
            break
            
        params = {"p": keyword, "b": (page-1)*30 + 1}
        try:
            print(f"    - Scraping Page {page}...")
            response = requests.get(url, params=params, headers=headers, timeout=15)
            print(f"    - [Status: {response.status_code}]")
            if response.status_code != 200:
                print(f"    - [Warning] Response snippet: {response.text[:200]}...")
            
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for ts in target_shops:
                if ts in results: continue
                
                search_term = ts[:10] if len(ts) > 10 else ts
                norm_search_term = normalize_text(search_term)
                
                shop_regex = re.compile(re.escape(search_term), re.I)
                shop_candidates = soup.find_all(['a', 'span'], string=shop_regex)
                shop_candidates += soup.select('a[class*="ItemStore"], span[class*="ItemStore"]')
                
                for s_elem in shop_candidates:
                    current_shop_text = s_elem.get_text(strip=True)
                    if not current_shop_text or norm_search_term not in normalize_text(current_shop_text):
                        continue
                        
                    item_container = s_elem.find_parent(lambda tag: tag.name == 'div' and (
                        tag.get('data-index') or 
                        any(c for c in tag.get('class', []) if 'SearchResultItem' in c or 'Item' in c or 'ItemCard' in c)
                    ))
                    
                    if not item_container:
                        item_container = s_elem.find_parent(['div', 'li'], class_=re.compile(r'Item|Result'))
                    
                    if not item_container: continue

                    # --- URL ---
                    url_elem = item_container.select_one('a[href*="store.shopping.yahoo.co.jp"]') or item_container.find('a', href=True)
                    url_path = url_elem.get('href') if url_elem else "#"
                    if url_path.startswith('/'): url_path = f"https://shopping.yahoo.co.jp{url_path}"
                    
                    name = "不明（要目視確認）"
                    # --- 商品名 ---
                    name_elem = item_container.select_one('[class*="ItemTitle"]') or item_container.select_one('h2')
                    if name_elem:
                        name = name_elem.get_text(strip=True)
                    else:
                        for a in item_container.find_all('a'):
                            a_txt = a.get_text(strip=True)
                            if len(a_txt) > 10 and normalize_text(current_shop_text) not in normalize_text(a_txt):
                                name = a_txt; break
                        if name == "不明（要目視確認）":
                            img = item_container.select_one('img[alt]')
                            if img: name = img['alt']
                    
                    # --- 価格 ---
                    price = 0
                    shipping = 0
                    
                    price_elem = item_container.select_one('[class*="Price"]') or item_container.select_one('[class*="priceText"]')
                    if price_elem:
                        ptxt = price_elem.get_text(strip=True)
                        price = int(re.sub(r'[^\d]', '', ptxt)) if re.search(r'\d', ptxt) else 0
                    
                    if price <= 0:
                        all_text = item_container.get_text()
                        price_matches = re.findall(r'[¥￥]\s*([\d,]+)|([\d,]+)\s*円', all_text)
                        extracted_vals = []
                        for m in price_matches:
                            vstr = m[0] or m[1]
                            v = int(re.sub(r'[^\d]', '', vstr))
                            if v > 100: extracted_vals.append(v)
                        if extracted_vals: price = max(extracted_vals)
                    
                    # --- 送料 ---
                    stxt = item_container.get_text()
                    if "送料無料" in stxt:
                        shipping = 0
                    else:
                        ship_match = re.search(r'[+＋]送料\s*([\d,]+)円', stxt)
                        if ship_match:
                            shipping = int(re.sub(r'[^\d]', '', ship_match.group(1)))
                        else:
                            ship_elem = item_container.select_one('[class*="Shipping"], [class*="shipping"]')
                            if ship_elem:
                                s_val_txt = ship_elem.get_text()
                                if "送料無料" in s_val_txt: shipping = 0
                                else:
                                    sm = re.search(r'([\d,]+)', s_val_txt)
                                    if sm: shipping = int(re.sub(r'[^\d]', '', sm.group(1)))
                    
                    total_price = price + shipping
                    
                    # --- 追加データの取得（詳細ページ巡回） ---
                    reviews, ranking = fetch_product_extra_info(url_path, headers)
                    
                    results[ts] = {
                        "item_name": name,
                        "price": price,
                        "shipping": shipping,
                        "total_price": total_price,
                        "url": url_path,
                        "reviews": reviews,
                        "ranking": ranking,
                        "status": "出品中" if price > 0 else "取得エラー（要確認）"
                    }
                    print(f"      [Hit] {ts}: ¥{total_price} | レビュー:{reviews}件 | 順位:{ranking}位")
                    break 
            
            if page < max_pages:
                time.sleep(random.uniform(1.2, 2.5))
                
        except Exception as e:
            print(f"    [Error] Page {page}: {e}")
            break
            
    return results

def fetch_product_extra_info(url, headers):
    """
    商品個別ページからレビュー件数とランキングを堅牢なロジックで取得する
    """
    reviews = 0
    ranking = 0
    
    if not url or url == "#":
        return reviews, ranking
        
    try:
        # サーバー負荷軽減のためわずかに待機
        time.sleep(random.uniform(0.5, 1.2))
        response = requests.get(url, headers=headers, timeout=10)
        
        # 診断ログ出力
        print(f"      [Detail Status: {response.status_code}]")
        print(f"      [Detail Snippet] {response.text[:200].replace('\\n', ' ')}...")
        save_debug_html(response.text) # デバッグ用に最新のページを保存

        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 診断ログ: ページタイトルの出力
        page_title = soup.title.text.strip() if soup.title else "No Title"
        print(f"      [Detail] Page: {page_title[:40]}...")

        # --- 1. JSON-LD (構造化データ) からの抽出 (最優先) ---
        try:
            scripts = soup.find_all('script', type='application/ld+json')
            for script in scripts:
                try:
                    data = json.loads(script.string)
                    # Product または AggregateRating 構造を探す
                    if isinstance(data, dict):
                        # パターンA: AggregateRating が直接入っている場合
                        if data.get('@type') == 'AggregateRating' and 'reviewCount' in data:
                            reviews = int(data['reviewCount'])
                            break
                        # パターンB: Product の中に aggregateRating がある場合
                        agg = data.get('aggregateRating')
                        if agg and 'reviewCount' in agg:
                            reviews = int(agg['reviewCount'])
                            break
                except: continue
            if reviews > 0:
                print(f"      [Debug] ReviewCount found via JSON-LD: {reviews}")
        except Exception as e:
            print(f"      [Debug] JSON-LD parse error: {e}")

        # --- 2. レビュー件数の取得 (HTMLフォールバック) ---
        if reviews == 0:
            review_selectors = [
                'a[href*="#anchor-reviewDetail"]',
                '.elReviewCount',
                '.RatingReview__count',
                '.ProductRate__reviewCount',
                'span[itemprop="reviewCount"]'
            ]
            for sel in review_selectors:
                elem = soup.select_one(sel)
                if elem:
                    text = elem.get_text(strip=True)
                    match = re.search(r'(\d+)', text.replace(',', ''))
                    if match:
                        reviews = int(match.group(1))
                        print(f"      [Debug] ReviewCount found via HTML ({sel}): {reviews}")
                        break

        # --- 3. ランキング順位の取得 (HTMLフォールバック) ---
        ranking_selectors = [
            'a[href*="categoryranking"]',
            '.elRanking',
            '.RankingLink',
            '.ProductRanking__rank',
            '[data-ranking]'
        ]
        for sel in ranking_selectors:
            elem = soup.select_one(sel)
            if elem:
                # リンクの場合は親要素のテキストも確認
                search_text = elem.get_text(strip=True)
                if 'categoryranking' in sel:
                    parent = elem.find_parent()
                    if parent: search_text = parent.get_text(strip=True)
                
                # 「ランキング 1 位」や「1位」などを抽出
                match = re.search(r'(?:ランキング)?\s*(\d+)\s*位', search_text.replace(',', ''))
                if not match:
                    # 数値のみのケースも想定
                    match = re.search(r'(\d+)', search_text.replace(',', ''))
                
                if match:
                    ranking = int(match.group(1))
                    print(f"      [Debug] Ranking found via HTML ({sel}): {ranking}")
                    break

        # 最終チェックログ
        if reviews == 0: print(f"      [Check] ReviewCount not found. URL: {url}")
        if ranking == 0: print(f"      [Check] Ranking not found. URL: {url}")
                
    except Exception as e:
        print(f"      [Detail Error] {url}: {e}")
        
    return reviews, ranking

def main():
    print(f"=== Daily Bot Execution Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    
    if not GAS_WEBAPP_URL:
        print("Error: GAS_WEBAPP_URL is not set.")
        return

    # 1. GASから検索ターゲットとショップリストを取得 (GET)
    try:
        print(f"[Get] Fetching search data from {GAS_WEBAPP_URL}...")
        response = requests.get(GAS_WEBAPP_URL, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        search_targets = data.get("targets", [])
        target_shops = data.get("shops", [])
        
        if not isinstance(search_targets, list):
            print(f"Error: Unexpected targets format (expected list)")
            return
            
        if not target_shops:
            print("Error: Target shops list is empty. Termination for safety.")
            return

        print(f"[Done] Fetched {len(search_targets)} targets and {len(target_shops)} shops.")
    except Exception as e:
        print(f"Failed to fetch data from GAS: {e}")
        return

    all_data_for_gas = []
    
    for target in search_targets:
        category = target.get("category", "不明")
        part_number = target.get("part_number")
        if not part_number:
            print("  [Skip] Empty part_number found.")
            continue
            
        print(f"\n> Target: {part_number} ({category})")
        found_data = {}
        max_retries = 3
        
        for attempt in range(max_retries):
            if attempt > 0:
                print(f"    - Retry {attempt}/{max_retries}...")
                time.sleep(2.0 + random.random())

            # 品番をキーワードとして使用
            current_results = fetch_benchmark_data(part_number, target_shops, max_pages=3)
            
            for shop, data in current_results.items():
                if shop not in found_data or (found_data[shop]["status"] != "出品中" and data["status"] == "出品中"):
                    found_data[shop] = data
            
            # すべてのショップが見つかったか確認
            complete_success = all(ts in found_data and found_data[ts].get("status") == "出品中" for ts in target_shops)
            if complete_success:
                print("    - Search complete.")
                break
    
        # データの集約
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for shop in target_shops:
            if shop in found_data:
                d = found_data[shop]
                all_data_for_gas.append({
                    "timestamp": timestamp,
                    "category": category,
                    "part_number": part_number,
                    "shop_name": shop,
                    "price": d["price"],
                    "shipping": d["shipping"],
                    "total_price": d["total_price"],
                    "url": d["url"],
                    "item_name": d["item_name"],
                    "reviews": d.get("reviews", 0),
                    "ranking": d.get("ranking", 0)
                })
            else:
                all_data_for_gas.append({
                    "timestamp": timestamp,
                    "category": category,
                    "part_number": part_number,
                    "shop_name": shop,
                    "price": 0,
                    "shipping": 0,
                    "total_price": 0,
                    "url": "",
                    "item_name": "（見つかりませんでした）",
                    "reviews": 0,
                    "ranking": 0
                })

    # GASへのデータ送信 (POST)
    payload = {
        "token": SECRET_TOKEN,
        "data": all_data_for_gas
    }
    
    if all_data_for_gas:
        try:
            print(f"\n[Post] Sending {len(all_data_for_gas)} records to GAS...")
            response = requests.post(GAS_WEBAPP_URL, json=payload, timeout=30)
            response.raise_for_status()
            print(f"[Done] GAS Response: {response.text}")
        except Exception as e:
            print(f"[Failed] GAS Transmission Error: {e}")
    else:
        print("\n[Skip] No data to send.")

    print(f"\n=== Daily Bot Execution Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

if __name__ == "__main__":
    main()
