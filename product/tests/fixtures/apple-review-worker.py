"""Synthetic worker for the isolated visual review; never opens EventKit or user data."""
import json
import sys
from pathlib import Path

root=Path(__file__).parent
for line in sys.stdin:
    request=json.loads(line)
    try:
        if request['action']=='initialize':value=True
        else:
            call=request['input'];method=call['method'];args=call.get('arguments',{});kind=args.get('kind','calendar')
            states=json.loads((root/'review-state.json').read_text());state=states[kind]
            if method!='status':
                with (root/'review-calls.jsonl').open('a') as stream:stream.write(json.dumps(call)+'\n')
                if (root/'review-fail').exists():raise RuntimeError('Apple 暂时无法连接，请稍后重试。')
            value=state
            if method=='resolve_category_conflict':
                state['category_conflicts']=[item for item in state.get('category_conflicts',[]) if item['apple_id']!=args['apple_id']]
                for link in state.get('links',[]):
                    if link['apple_id']==args['apple_id']:link.pop('name_conflict',None)
            elif method=='resolve_conflict':state['conflicts']=[]
            elif method=='cancel_operation':state['operations']=[]
            elif method=='disconnect':state['connected']=False
            elif method=='start_sync':
                state.update(connected=True,auto_enabled=True)
                value={'state':state,'result':{'status':'success'}}
            elif method!='status':raise ValueError('Unexpected fixture operation: '+method)
            if method!='status':
                pending=root/'review-state-worker.next'
                pending.write_text(json.dumps(states));pending.replace(root/'review-state.json')
        result={'id':request['id'],'ok':True,'value':value}
    except Exception as error:result={'id':request['id'],'ok':False,'error':str(error)}
    print(json.dumps(result),flush=True)
