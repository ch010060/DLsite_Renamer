from tkinter import filedialog
from tkinter import messagebox
from lxml import html
from glob import glob
from fake_useragent import UserAgent
import tkinter as tk
import threading
import requests
import time
import re
import os
import json

import sys
import argparse
import urllib.request

# 默認設定
template_RJ = 'workno title '  # 默認RJ命名模板(Voice)
template_BJ = 'workno title '  # 默認BJ命名模板(Comic)
template_VJ = 'workno title '  # 默認VJ命名模板(Game)

replace_rules = []  # 替換規則

RJ_WEBPATH = 'https://www.dlsite.com/maniax/work/=/product_id/'
RJ_G_WEBPATH = 'https://www.dlsite.com/home/work/=/product_id/'
BJ_WEBPATH = 'https://www.dlsite.com/books/work/=/product_id/'
BJ_G_WEBPATH = 'https://www.dlsite.com/comic/work/=/product_id/'
VJ_WEBPATH = 'https://www.dlsite.com/pro/work/=/product_id/'
VJ_G_WEBPATH = 'https://www.dlsite.com/soft/work/=/product_id/'
R_COOKIE = {'adultchecked': '1'}

# re.compile()返回一個匹配對像
# ensure path name is exactly RJ?(\d{8}d{7}d{6}) or BJ?(\d{8}d{7}d{6}) or VJ?(\d{8}d{7}d{6})
pattern = re.compile("([BRV][EJ])?(\d{8}|\d{7}|\d{6})|$")
# filter to substitute illegal filenanme characters to " "
filter = re.compile('[\\\/:"*?<>|]+')


# 避免ERROR: Max retries exceeded with url
requests.adapters.DEFAULT_RETRIES = 5  # 增加重連次數
s = requests.session()
s.keep_alive = False  # 關閉多餘連接
# s.get(url) # 你需要的網址


# 把字串半形轉全形
def strB2Q(s):
    rstring = ""
    for uchar in s:
        u_code = ord(uchar)
        if u_code == 32:  # 全形空格直接轉換
            u_code = 12288
        elif 33 <= u_code <= 126:  # 全形字元（除空格）根據關係轉化
            u_code += 65248
        rstring += chr(u_code)
    return rstring

def match_code(code, user_agent):
    # requests函示庫是一個常用於http請求的模組
    if code[0] == "R":
        url = RJ_WEBPATH + code
    if code[0] == "B":
        url = BJ_WEBPATH + code
    if code[0] == "V":
        url = VJ_WEBPATH + code
    try:
        headers = {
            'user-agent': user_agent
        }
        # allow_redirects=False 禁止重定向
        r = s.get(url, allow_redirects=True, cookies=R_COOKIE, headers=headers)
        # HTTP狀態碼==200表示請求成功
        if r.status_code != 200:
            #print("    Status code:", r.status_code, "\nurl:", url)
            try:
                ## 改成一般向網址
                if code[0] == "R":
                    url = RJ_G_WEBPATH + code
                if code[0] == "B":
                    url = BJ_G_WEBPATH + code
                if code[0] == "V":
                    url = VJ_G_WEBPATH + code
                r = s.get(url, allow_redirects=False, cookies=R_COOKIE)
                if r.status_code != 200:
                    return r.status_code, "", "", "", [], [], "", "", ""
            except os.error as err:
                print("**請求超時!\n")
                print("  請檢查網絡連接\n")
                return "", "", "", "", [], [], "", "", ""

        # fromstring()在解析xml格式時, 將字串轉換為Element對像, 解析樹的根節點
        # 在python中, 對get請求返回的r.content做fromstring()處理, 可以方便進行後續的xpath()定位等
        tree = html.fromstring(r.content)
        img_url = ""
        try:
            img_url = tree.xpath('//meta[@property="og:image"]/@content')[0]
        except IndexError:
            try:
                # Fallback to the original twitter meta tag
                img_url = tree.xpath('//meta[@name="twitter:image:src"]/@content')[0]
            except IndexError:
                print("**作品封面不存在!\n")
                img_url = ""
        
        if img_url and img_url.startswith('//'):
            img_url = 'https:' + img_url
        title = tree.xpath('//h1[@id="work_name"]/text()')[0]
        circle = tree.xpath(
            '//span[@itemprop="brand" and @class="maker_name"]/*/text()')[0]
        cvList = tree.xpath(
            '//*[@id="work_outline"]/tr/th[contains(text(), "声優")]/../td/a/text()')
        authorList = tree.xpath(
            '//*[@id="work_maker"]/tr/th[contains(text(), "著者")]/../td/a/text()')
        type = tree.xpath(
            '//*[@id="work_outline"]/tr/th[contains(text(), "作品形式")]/../td/div/a/span/text()')[0]
        # 精簡遊戲類型
        game_type_list = ["アクション", "クイズ", "アドベンチャー", "ロールプレイング", "テーブル", "デジタルノベル", "シミュレーション", "タイピング", "シューティング", "パズル", "その他ゲーム"]
        if type in game_type_list:
            type = "ゲーム"
            
        work_age = tree.xpath(
            '//*[@id="work_outline"]/tr/th[contains(text(), "年齢指定")]/../td/div/a/span/text()')
        if not work_age:
            work_age = tree.xpath(
                '//*[@id="work_outline"]/tr/th[contains(text(), "年齢指定")]/../td/div/span/text()')
        release_date = tree.xpath(
            '//*[@id="work_outline"]/tr/th[contains(text(), "販売日")]/../td/a/text()')[0]
        # 精簡日期: 20ab年cd月ef日 => abcdef
        if len(release_date) >= 11:
            release_date = release_date[2]+release_date[3]+release_date[5]+release_date[6]+release_date[8]+release_date[9]

        return 200, img_url, title, circle, cvList, authorList, work_age[0], release_date, type
            
    except os.error as err:
        print("**請求超時!\n")
        print("  請檢查網絡連接\n")
        return "", "", "", "", [], [], "", "", ""

def nameChange(path, del_flag, cover_flag, recur_flag):
        ua = UserAgent()
        print("選擇路徑: " + path + "\n")
        # os.listdir()返回指定的資料夾包含的檔案或資料夾的名字的列表
        if recur_flag: # 遞迴檢索
            files = [y for x in os.walk(path) for y in glob(os.path.join(x[0], '*'))]
        else: # 根目錄檢索
            files = os.listdir(path)
        for file in files:
                if recur_flag: # 遞迴檢索需要修正路徑
                    path = os.path.split(file)[0]
                # 嘗試獲取code
                code_list = re.findall(pattern, file.upper())[0]
                code = ''.join(code_list)
                # 如果沒能提取到code
                if not code:
                    continue  # 跳過該資料夾/檔案
                else:
                    user_agent = ua.random
                    #print('Processing: ' + code)
                    print('Processing: ' + code + '\n')
                    r_status, img_url, title, circle, cvList, authorList, work_age, release_date, type = match_code(code, user_agent)
                    # 如果順利爬取網頁訊息
                    if r_status == 200 and title and circle:
                        if del_flag:
                            # 刪除title中的【.*?】
                            title = re.sub(u"\\【.*?】", "", title)

                        if code[0] == "R":
                            new_name = template_RJ.replace("workno", code)
                        if code[0] == "B":
                            new_name = template_BJ.replace("workno", code)
                        if code[0] == "V":
                            new_name = template_VJ.replace("workno", code)

                        new_name = new_name.replace("title", title)
                        new_name = new_name.replace("circle", circle)
                        new_name = new_name.replace("work_age", work_age)
                        new_name = new_name.replace("release_date", release_date)
                        new_name = new_name.replace("type", type)

                        author = ""
                        if authorList:  # 如果authorList非空
                            for name in authorList:
                                author += "," + name
                            new_name = new_name.replace("author", author[1:])
                        else:
                            new_name = new_name.replace("(author)", "")  

                        cv = ""
                        if cvList:  # 如果cvList非空
                            for name in cvList:
                                cv += "," + name
                            new_name = new_name.replace("cv", cv[1:])
                        else:
                            new_name = new_name.replace("(CV. cv)", "")


                        # 要下載封面且是資料夾
                        if cover_flag and img_url and os.path.isdir(os.path.join(path, file)):
                            try: # 嘗試下載封面
                                store_path = os.path.join(path, file, "cover.jpg")
                                if not os.path.isfile(store_path):
                                    print("  下載封面...\n")
                                    opener = urllib.request.build_opener()
                                    opener.addheaders = [('User-agent', user_agent)]
                                    urllib.request.install_opener(opener)
                                    urllib.request.urlretrieve(img_url, store_path)
                                else:
                                    print("**封面已存在，跳過下載!\n")
                            except os.error as err:
                                print("**下載封面過程中出現錯誤!\n")

                        # 1. 將Windows文件名中的非法字元替換成空白
                        # re.sub(pattern, repl, string)
                        # new_name = re.sub(filter, " ", new_name)
                          
                        # 1. 將Windows文件名中的非法字元替換成全形
                        # re.match(pattern, string, flags=0)
                        fixed_filename = "";
                        for char in new_name:
                            if re.match(filter, char):
                                fixed_filename += strB2Q(char)
                            else:
                                fixed_filename += char
                                
                        # 2. 多空格轉單空格
                        new_name = ' '.join(fixed_filename.split())
                        
                        # 嘗試重命名
                        try:
                            # strip() 去掉字串兩邊的空格
                            if os.path.isfile(os.path.join(path, file)):  # 如果是檔案
                                temp, file_extension = os.path.splitext(file)
                                os.rename(os.path.join(path, file),
                                        os.path.join(path, new_name.strip()+file_extension))
                            else:  # 如果是資料夾
                                os.rename(os.path.join(path, file),
                                        os.path.join(path, new_name.strip()))
                        except os.error as err:
                            print("**重命名失敗!\n")
                            print("  " + os.path.join(path, file) + "\n")
                            print("  請檢查是否存在重複的名稱\n")
                    elif r_status == 404:
                        print("**爬取DLsite過程中出現錯誤!\n")
                        print("  請檢查本作是否已經下架或被收入合集\n")
                    elif r_status != "":
                        print("**爬取DLsite過程中出現錯誤!\n")
                        print("  網頁 URL: " +
                                    RJ_WEBPATH + code + "\n")
                        print("  HTTP 狀態碼: " +
                                    str(r_status) + "\n")

                    # set delay to avoid being blocked from server
                    time.sleep(0.1)

        print("*******完成!*******\n\n\n\n")
        
def dir_path(path):
    if os.path.isdir(path):
        return path
    else:
        raise argparse.ArgumentTypeError(f"\"{path}\" is not a valid path!")
        
def process_command():        
    parser = argparse.ArgumentParser(description="Renamer for DLsite works v3.7")
    parser.add_argument('-d', "--DEL", action='store_true', help='delete string in 【】')
    parser.add_argument('-c', "--COVER", action='store_true', help='download cover')
    parser.add_argument('-r', "--RECUR", action='store_true', help='recursively processing')
    parser.add_argument('-i', "--PATH", type=dir_path, required=True, help='path for processing')
    return parser.parse_args()

args = process_command()

print("===讀取配置文件===\n\n")
# 讀取配置文件
# os.path.dirname(__file__) 當前腳本所在路徑
basedir = os.path.abspath(os.path.dirname(__file__))
try:
    fname = os.path.join(basedir, 'config.json')
    with open(fname, 'r', encoding='utf-8') as f:
        config = json.load(f)
        for tag in config['replace_rules']:  # 模板非空
            if ("workno" in tag['to']):
                if tag['type'] == "rj":
                    print("**使用自定義RJ命名模板:\n")
                    template_RJ = tag['to']
                    print("  " + template_RJ.strip() + "\n\n")
                if tag['type'] == "bj":
                    print("**使用自定義BJ命名模板:\n")
                    template_BJ = tag['to']
                    print("  " + template_BJ.strip() + "\n\n")
                if tag['type'] == "vj":
                    print("**使用自定義VJ命名模板:\n")
                    template_VJ = tag['to']
                    print("  " + template_VJ.strip() + "\n\n")
            else:
                print("**模板格式錯誤: 模板中必須包含\"workno\"!\n")
                print("  使用默認命名模板:\n")
                print("  workno title \n\n")

        if config["replace_rules"] and type(config["replace_rules"]) == list and len(config["replace_rules"]):
            replace_rules = config["replace_rules"]

except os.error as err:
    # 生成配置文件
    json_data = {
        "replace_rules":
        [
            {
                "type": "rj",
                "from": "",
                "to": "workno title "
            },
            {
                "type": "bj",
                "from": "",
                "to": "workno title "
            },
            {
                "type": "vj",
                "from": "",
                "to": "workno title "
            }
        ]
    }
    with open(fname, "w", encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, sort_keys=False,indent=4)
        print("**使用默認命名模板:\n")
        print("  workno title \n")

nameChange(args.PATH,args.DEL,args.COVER,args.RECUR)