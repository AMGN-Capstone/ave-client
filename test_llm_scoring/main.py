import os
import sys
import csv
import json
import time
from dotenv import load_dotenv

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(os.path.join(parent_dir, "test_yt_scripter"))
sys.path.append(os.path.join(parent_dir, "test_llm_gateway"))

from yt_scripter import Scripter
from llm_gateway import LLMGateway

# ==============================================================================
VIDEO_URL = "https://www.youtube.com/watch?v=3tejmt47Hkw"
LANGUAGE = "ko"
BATCH_SIZE = 100
MODEL_NAME = "deepseek-chat"
MAX_RETRIES = 3
# ==============================================================================

def load_prompt():
    prompt_path = os.path.join(current_dir, "prompt.txt")
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read().strip()

def chunk_list(data_list, chunk_size):
    for i in range(0, len(data_list), chunk_size):
        yield data_list[i:i + chunk_size]

def main():
    env_path = os.path.join(parent_dir, "test_llm_gateway", ".env")
    load_dotenv(env_path)

    try:
        scripter = Scripter(method="auto")
        gateway = LLMGateway(provider="deepseek")
    except Exception as e:
        print(f"🔴 초기화 오류: {e}")
        return

    print(f"🔵 스크립트 추출 시작: {VIDEO_URL}")
    data_dir = os.path.join(current_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    
    script_result = scripter.process(video_url=VIDEO_URL, target_dir=data_dir, lang=LANGUAGE)
    
    if "error" in script_result or not script_result.get("success"):
        print(f"🔴 스크립트 추출 실패: {script_result.get('error')}")
        return
        
    csv_path = script_result["csv_path"]
    
    base_name = os.path.splitext(os.path.basename(csv_path))[0]
    
    print(f"🟢 스크립트 추출 완료: {csv_path} (총 {script_result.get('row_count')}행)")

    csv_data = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            csv_data.append(row)

    system_prompt = load_prompt()
    all_scored_results = []
    
    chunks = list(chunk_list(csv_data, BATCH_SIZE))
    
    print(f"\n🔵 스크립트 점수 평가를 시작합니다. (총 {len(chunks)}개 청크, 크기: {BATCH_SIZE})")
    
    for chunk_idx, chunk in enumerate(chunks, 1):
        chunk_filename = f"{base_name}_{BATCH_SIZE}_{chunk_idx}.csv"
        chunk_filepath = os.path.join(data_dir, chunk_filename)
        
        if os.path.exists(chunk_filepath):
            print(f"  ⏭️ 청크 {chunk_idx}/{len(chunks)} 스킵 (기존 처리 결과 존재: {chunk_filename})")
            with open(chunk_filepath, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    all_scored_results.append({"index": row["index"], "score": int(row["score"])})
            continue

        input_data = [{"index": row["index"], "text": row["text"]} for row in chunk]
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(input_data, ensure_ascii=False)}
        ]

        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            print(f"  ⏳ 청크 {chunk_idx}/{len(chunks)} 처리 중... (시도 {attempt}/{MAX_RETRIES})")
            
            response_text = gateway.request(
                messages=messages,
                model=MODEL_NAME,
                temperature=0.3, 
                response_format={"type": "json_object"}
            )

            try:
                clean_response = response_text.replace("```json", "").replace("```", "").strip()
                parsed_json = json.loads(clean_response)
                scored_items = parsed_json.get("results", [])

                if len(scored_items) == len(chunk):
                    all_scored_results.extend(scored_items)
                    success = True
                    print(f"    🟢 통과 (항목 수 일치: {len(scored_items)})")
                    
                    with open(chunk_filepath, "w", encoding="utf-8-sig", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=["index", "text", "score"])
                        writer.writeheader()
                        for item, orig_row in zip(scored_items, chunk):
                            writer.writerow({
                                "index": item["index"],
                                "text": orig_row["text"],
                                "score": item["score"]
                            })
                    break
                else:
                    print(f"    🟡 검증 실패: 입력 {len(chunk)}개 != 출력 {len(scored_items)}개. 재시도합니다.")
            except json.JSONDecodeError:
                print(f"    🟡 검증 실패: JSON 파싱 오류. 재시도합니다.")
            
            time.sleep(2)

        if not success:
            print(f"🔴 청크 {chunk_idx} 처리 최종 실패. 평가를 중단합니다. (재시작 시 이어서 진행됩니다)")
            return

    print("\n🔵 결과 병합 및 최종 파일 저장 중...")
    
    score_dict = {str(item["index"]): item["score"] for item in all_scored_results}
    
    final_csv_path = os.path.join(data_dir, f"{base_name}_scored.csv")
    
    with open(final_csv_path, "w", encoding="utf-8-sig", newline="") as f:
        fieldnames = ["index", "start", "end", "text", "score"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for row in csv_data:
            idx_str = str(row["index"])
            row["score"] = score_dict.get(idx_str, -1)
            writer.writerow(row)

    print(f"🟢 모든 처리가 완료되었습니다. 통합 결과 저장: {final_csv_path}")

if __name__ == "__main__":
    main()
