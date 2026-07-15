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

def load_prompt(filename="prompt.txt"):
    prompt_path = os.path.join(current_dir, filename)
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
    csv_dict = {}
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            csv_data.append(row)
            csv_dict[int(row["index"])] = row

    system_prompt = load_prompt("prompt.txt")
    merge_prompt = load_prompt("prompt_merge.txt")
    
    all_clusters = []
    chunks = list(chunk_list(csv_data, BATCH_SIZE))
    total_chunks = len(chunks)
    
    boundary_indices = set()
    for chunk in chunks[:-1]:
        boundary_indices.add(int(chunk[-1]["index"]))
    
    print(f"\n🔵 [Phase 1] 1차 문맥 클러스터링을 시작합니다. (총 {total_chunks}개 청크, 크기: {BATCH_SIZE})")
    
    for chunk_idx, chunk in enumerate(chunks, 1):
        chunk_filename = f"{base_name}_{chunk_idx}_{total_chunks}.csv"
        chunk_filepath = os.path.join(data_dir, chunk_filename)
        
        if os.path.exists(chunk_filepath):
            print(f"  ⏭️ 청크 {chunk_idx}/{total_chunks} 스킵 (기존 처리 결과 존재: {chunk_filename})")
            with open(chunk_filepath, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    all_clusters.append({
                        "index_start": int(row["index_start"]),
                        "index_end": int(row["index_end"]),
                        "start": row["start"],
                        "end": row["end"],
                        "text": row["text"],
                        "score": int(row["score"])
                    })
            continue

        input_data = [{"index": int(row["index"]), "text": row["text"]} for row in chunk]
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(input_data, ensure_ascii=False)}
        ]

        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            print(f"  ⏳ 청크 {chunk_idx}/{total_chunks} 처리 중... (시도 {attempt}/{MAX_RETRIES})")
            
            response_text = gateway.request(
                messages=messages,
                model=MODEL_NAME,
                temperature=0.3, 
                response_format={"type": "json_object"}
            )

            try:
                clean_response = response_text.replace("```json", "").replace("```", "").strip()
                parsed_json = json.loads(clean_response)
                clusters = parsed_json.get("clusters", [])

                if len(clusters) > 0:
                    chunk_clusters = []
                    for c in clusters:
                        s_idx, e_idx = int(c["start_index"]), int(c["end_index"])
                        score = int(c.get("score", 0))
                        
                        if s_idx in csv_dict and e_idx in csv_dict:
                            start_time = csv_dict[s_idx]["start"]
                            end_time = csv_dict[e_idx]["end"]
                            
                            combined_texts = [csv_dict[i]["text"] for i in range(s_idx, e_idx + 1) if i in csv_dict]
                            
                            chunk_clusters.append({
                                "index_start": s_idx,
                                "index_end": e_idx,
                                "start": start_time,
                                "end": end_time,
                                "text": " ".join(combined_texts),
                                "score": score
                            })
                    
                    all_clusters.extend(chunk_clusters)
                    success = True
                    print(f"    🟢 통과 (클러스터 {len(chunk_clusters)}개 생성)")
                    
                    with open(chunk_filepath, "w", encoding="utf-8-sig", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=["index_start", "index_end", "start", "end", "text", "score"])
                        writer.writeheader()
                        for c in chunk_clusters:
                            writer.writerow(c)
                    break
                else:
                    print(f"    🟡 검증 실패: 빈 클러스터 반환. 재시도합니다.")
            except json.JSONDecodeError:
                print(f"    🟡 검증 실패: JSON 파싱 오류. 재시도합니다.")
            
            time.sleep(2)

        if not success:
            print(f"🔴 청크 {chunk_idx} 처리 최종 실패. 평가를 중단합니다.")
            return

    all_clusters.sort(key=lambda x: x["index_start"])

    print(f"\n🔵 [Phase 2] 청크 경계선 문맥 단절 2차 검토를 시작합니다.")
    
    i = 0
    merge_count = 0
    while i < len(all_clusters) - 1:
        c1 = all_clusters[i]
        c2 = all_clusters[i+1]
        
        if c1["index_end"] in boundary_indices:
            
            c1_end_row_idx = next((idx for idx, row in enumerate(csv_data) if int(row["index"]) == c1["index_end"]), -1)
            
            if c1_end_row_idx != -1 and c1_end_row_idx + 1 < len(csv_data):
                expected_next_index = int(csv_data[c1_end_row_idx + 1]["index"])
                
                if c2["index_start"] == expected_next_index:
                    print(f"  🔍 검토 중: 클러스터({c1['index_start']}~{c1['index_end']}) <-> 클러스터({c2['index_start']}~{c2['index_end']})")
                    
                    # LLM 병합 요청
                    input_data = {
                        "Cluster_1": {"text": c1["text"]},
                        "Cluster_2": {"text": c2["text"]}
                    }
                    messages = [
                        {"role": "system", "content": merge_prompt},
                        {"role": "user", "content": json.dumps(input_data, ensure_ascii=False)}
                    ]
                    
                    for attempt in range(1, MAX_RETRIES + 1):
                        response_text = gateway.request(
                            messages=messages,
                            model=MODEL_NAME,
                            temperature=0.2,
                            response_format={"type": "json_object"}
                        )
                        try:
                            clean_res = response_text.replace("```json", "").replace("```", "").strip()
                            merge_res = json.loads(clean_res)
                            
                            is_merge = merge_res.get("merge", False)
                            reason = merge_res.get("reason", "")
                            
                            if is_merge:
                                new_score = merge_res.get("new_score", c1["score"])
                                print(f"    🟢 병합 결정! (사유: {reason}) -> 새로운 점수: {new_score}")
                                
                                merged_cluster = {
                                    "index_start": c1["index_start"],
                                    "index_end": c2["index_end"],
                                    "start": c1["start"],
                                    "end": c2["end"],
                                    "text": c1["text"] + " " + c2["text"],
                                    "score": int(new_score)
                                }
                                
                                all_clusters[i] = merged_cluster
                                del all_clusters[i+1]
                                merge_count += 1
                                
                                break
                            else:
                                print(f"    🔴 유지 결정 (사유: {reason})")
                                break
                        except json.JSONDecodeError:
                            print(f"    🟡 검증 실패: JSON 파싱 오류. 재시도합니다.")
                            time.sleep(2)
        
        i += 1

    print(f"\n💡 2차 검토 완료: 총 {merge_count}건의 경계선 병합이 수행되었습니다.")

    final_csv_path = os.path.join(data_dir, f"{base_name}_clustered.csv")
    with open(final_csv_path, "w", encoding="utf-8-sig", newline="") as f:
        fieldnames = ["index_start", "index_end", "start", "end", "text", "score"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for c in all_clusters:
            writer.writerow(c)

    print(f"🟢 모든 처리가 완료되었습니다. 통합 결과 저장: {final_csv_path}")

if __name__ == "__main__":
    main()
