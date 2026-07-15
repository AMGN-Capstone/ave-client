import os
import re
import csv

class AutoScripterPostProcessor:
    def process(self, txt_filepath: str, csv_filepath: str) -> dict:
        if not os.path.exists(txt_filepath):
            return {"success": False, "error": f"텍스트 파일을 찾을 수 없습니다: {txt_filepath}"}
            
        try:
            with open(txt_filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            processed_data = []
            current_start = ""
            current_end = ""
            current_text = []
            
            for line in lines:
                line = line.strip()
                
                if '-->' in line:
                    self._extract_and_append(current_start, current_end, current_text, processed_data)
                    
                    parts = line.split('-->')
                    current_start = parts[0].strip()
                    current_end = parts[1].split()[0].strip() 
                    current_text = []
                    
                elif line in ["WEBVTT", ""] or line.startswith("Kind:") or line.startswith("Language:"):
                    continue
                else:
                    current_text.append(line)

            self._extract_and_append(current_start, current_end, current_text, processed_data)

            with open(csv_filepath, 'w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Start Time', 'End Time', 'Text']) 
                for row in processed_data:
                    writer.writerow(row)

            return {"success": True, "csv_path": csv_filepath, "row_count": len(processed_data)}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _extract_and_append(self, start: str, end: str, text_lines: list, processed_data: list):
        if not start or not text_lines:
            return
            
        raw_text = " ".join(text_lines).replace('\u00a0', ' ').strip()
        
        clean_text = re.sub(r'\[.*?\]|\(.*?\)', '', raw_text).strip()
        
        if not clean_text or '<' in clean_text:
            return
            
        if processed_data and processed_data[-1][2] == clean_text:
            return
            
        processed_data.append([start, end, clean_text])
