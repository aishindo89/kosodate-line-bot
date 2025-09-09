# main.py
import os
import json
import google.generativeai as genai
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from dotenv import load_dotenv

# --- ▼▼▼ Firestore関連のライブラリを追加 ▼▼▼ ---
import firebase_admin
from firebase_admin import credentials, firestore
# --- ▲▲▲ ここまで ▲▲▲ ---

load_dotenv()

app = Flask(__name__)

# --- ▼▼▼ Firestoreの初期化処理を追加 ▼▼▼ ---
try:
    # Render環境では環境変数から認証情報を読み込む
    firebase_credentials_json = os.getenv('FIREBASE_CREDENTIALS')
    if firebase_credentials_json:
        firebase_credentials = json.loads(firebase_credentials_json)
        cred = credentials.Certificate(firebase_credentials)
    else:
        # ローカル環境ではファイルから読み込む
        cred = credentials.Certificate('serviceAccountKey.json')
    
    firebase_admin.initialize_app(cred)
    db = firestore.client()
except Exception as e:
    app.logger.error(f"Firebaseの初期化に失敗しました: {e}")
    db = None
# --- ▲▲▲ ここまで ▲▲▲ ---

# ユーザーごとの会話履歴を保存するための辞書
conversation_history = {}

# services.json のみを読み込む
try:
    with open('services.json', 'r', encoding='utf-8') as f:
        services_data = json.load(f)
except FileNotFoundError:
    services_data = []

def search_services(query):
    results = []
    for service in services_data:
        match_count = 0
        for keyword in service.get('keywords', []):
            if keyword in query:
                match_count += 1
        if service['name'] in query:
            match_count += 2
        
        if match_count > 0:
            results.append({'service': service, 'score': match_count})
    
    sorted_results = sorted(results, key=lambda x: x['score'], reverse=True)
    return [item['service'] for item in sorted_results[:3]]

system_prompt = """
あなたは「アイちゃん」です。元保育士で、現在は静岡県富士市役所に勤務しているという設定の、親しみやすいAIアシ-スタントです。
あなたの主な仕事は、富士市に住む子育て中のパパやママをサポートすることです。会話を通じて、ユーザーの悩みに寄り添い、解決の手助けをしてください。

# 振る舞いの基本ルール
- 必ず「アイちゃん」として応答してください。
- 常に丁寧語を使い、共感と優しさを忘れないでください。
- **市の公式サービスや施設について言及する際は、必ず提供された公式情報や、あなたがウェブ検索で見つけた公式サイトのURLを忠実に使用してください。架空のURLや例（example.comなど）を決して生成してはいけません。これは最も重要なルールです。**
- 医療的な判断が必要な相談には直接答えず、「かかりつけの小児科医や、市の保健センターにご相談くださいね」と優しく促してください。
- 回答の最後は、ユーザーを応援するポジティブな言葉で締めくくることが多いです。
- 全体の回答は250文字以内を目安に、簡潔にまとめてください。
"""


GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')

if not all([GEMINI_API_KEY, LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN]):
    raise ValueError("環境変数が設定されていません。")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash', system_instruction=system_prompt)

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id 
    user_message = event.message.text
    
    if user_id not in conversation_history:
        conversation_history[user_id] = model.start_chat(history=[])
    chat_session = conversation_history[user_id]
    
    relevant_services = search_services(user_message)
    
    context_prompt = f"ユーザーは「{user_message}」と質問しています。\n\n"

    # AIに思考プロセスを段階的に指示する、より構造化されたプロンプト
    context_prompt += "# あなたへの指示\n"
    context_prompt += "以下の思考プロセスとルールに従って、ユーザーへの最適な回答を生成してください。\n\n"

    context_prompt += "### ステップ1: ユーザーの意図を分析する\n"
    context_prompt += "ユーザーの質問は、以下のa, b, cのどれに最も近いか、まず判断してください。\n"
    context_prompt += "  a) 富士市の公式行政サービスに関する具体的な質問（例：「児童手当について」）\n"
    context_prompt += "  b) 富士市内の特定の施設（病院、公園、お店など）を探している質問（例：「近くの小児科は？」）\n"
    context_prompt += "  c) 一般的な子育ての悩みや、漠然とした相談（例：「夜泣きがひどい」「疲れた」）\n\n"

    context_prompt += "### ステップ2: 意図に基づいて行動を決定する\n"
    context_prompt += "- **意図が(a)の場合:** 下記の「公式情報リスト」を**最優先**で参照し、関連する情報を**正確に**伝えてください。\n"
    context_prompt += "- **意図が(b)の場合:** あなたのウェブ検索能力を使い、**富士市内**の関連施設を1〜2件探してください。その際、**必ず施設の正式名称**と**公式サイトのURL**を提示してください。\n"
    context_prompt += "- **意図が(c)の場合:** まず共感の言葉を述べ、一般的なアドバイスを生成します。その上で、もし下記の「公式情報リスト」に役立ちそうな情報があれば、追加で紹介することを検討してください。\n\n"

    # --- ▼▼▼ ここが今回の重要な修正点です ▼▼▼ ---
    # 複数行にわたる文字列を正しく結合するように修正
    context_prompt += (
        "### ステップ3: 回答を生成する際の共通ルール\n"
        "- **【最重要: URLの正確性】** 公式情報リストの情報を使う場合も、ウェブ検索をする場合も、**URLは絶対に創作しないでください。** 不明な場合は正直に「公式サイトURL不明」と記載してください。\n"
        "- **【対話の深化】** もしユーザーの質問が曖昧で追加情報が必要な場合、具体的な質問をしてください。ただし、同じパターンの質問を2回以上連続して使わず、3回目以降は「他に何かお聞きになりたいことはありますか？」のように表現を変えてください。\n"
        "- **【応答の自然さ】** 役立つ情報が見つからなくても、そのこと自体には言及せず、自然な会話を続けてください。\n"
        "- 最後に、上記のすべてを考慮して、一つの自然でまとまりのある文章として回答を作成してください。\n\n"
    )
    # --- ▲▲▲ ここまでが修正点です ▲▲▲ ---

    if relevant_services:
        context_prompt += "# あなたが利用できる富士市の公式情報リスト（信頼できる情報源）\n"
        for service in relevant_services:
            context_prompt += f"- サービス名: {service['name']}\n  - 説明: {service['description']}\n  - URL: {service['url']}\n"
        context_prompt += "\n"

    try:
        response = chat_session.send_message(context_prompt)
        gemini_reply = response.text

        if db:
            try:
                doc_ref = db.collection('consultations').document()
                doc_ref.set({
                    'user_id': user_id, 
                    'user_message': user_message,
                    'gemini_reply': gemini_reply,
                    'timestamp': firestore.SERVER_TIMESTAMP
                })
            except Exception as e:
                app.logger.error(f"Firestoreへの書き込みに失敗しました: {e}")

    except Exception as e:
        app.logger.error(f"Gemini API Error: {e}")
        gemini_reply = "ごめんなさい、今ちょっと考えがまとまらないみたいです。少し時間を置いてからもう一度試してみてください。"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=gemini_reply)]
            )
        )

if __name__ == "__main__":
    app.run(port=8080)

