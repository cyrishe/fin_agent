"""Optional small DS Flash audit. Does not alter deterministic labels or claim ground truth."""
import json,time
from pathlib import Path
from src.utils.ai_service import _create_llm_completion,DEFAULT_FLASH_MODEL,build_multimodal_user_message
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'docs/research/kline_pattern_library_20260928'
def main():
 from .catalog import CATALOG
 p=next(p for p in CATALOG if p['id']=='engulf_bull')
 path=OUT/'images/engulf_bull_hit_1.png'
 prompt='你在测试图表读取能力。根据图片独立判断最后两根蜡烛是否为阳包阴：前阴后阳，后根实体覆盖前根实体。请先描述最后两根颜色、开收关系，再说明是否足以看清；若像素不足请明确说不能可靠确认。红色代表收高于开，绿色代表收低于开。不要根据标题猜结论，不作交易建议。'
 started=time.perf_counter();result={'model':DEFAULT_FLASH_MODEL,'purpose':'image-input capability probe; one actual market chart','case_id':'engulf_bull_hit_1','prompt':prompt}
 try:
  response=_create_llm_completion([build_multimodal_user_message(text=prompt,image_paths=[path])],model=DEFAULT_FLASH_MODEL,max_tokens=1400,temperature=0,enable_think=False)
  result.update({'response':response.choices[0].message.content,'usage':response.usage.model_dump() if response.usage else None,'seconds':time.perf_counter()-started,'api_accepted_image':True})
 except Exception as e:
  # Only short provider message, never entire request, base64 image or credential-bearing URL.
  body=getattr(e,'body',None);message=body.get('message','') if isinstance(body,dict) else ''
  result.update({'api_accepted_image':False,'error_class':type(e).__name__,'error_message':message[:350],'seconds':time.perf_counter()-started})
 (OUT/'ds_flash_probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
 print(json.dumps(result,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
