"""Optional integration with the sibling platform checkout; portable unit tests need neither Node nor that repo."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_collector import FakeClient,LEAGUE,TEAM
from season_collector import Collector
from season_exports import export_all

NODE=shutil.which('node')
MODEL=Path(__file__).resolve().parents[2]/'nba-fantasy-platform/hosting/source/history-model.mjs'


@unittest.skipUnless(NODE and MODEL.is_file(),'Optional sibling platform and Node are not installed')
class PlatformContractTests(unittest.TestCase):
    def test_generated_season_normalizes_without_losing_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            season=Collector(FakeClient(),tmp,today=date(2025,11,1),progress=lambda _:None).collect(LEAGUE,TEAM)
            script="const {normalizeSeason}=await import(process.argv[1]);let raw='';for await(const c of process.stdin)raw+=c;console.log(JSON.stringify(normalizeSeason(JSON.parse(raw))));"
            result=subprocess.run([NODE,'--input-type=module','-e',script,MODEL.as_uri()],input=json.dumps(season),encoding='utf-8',capture_output=True,check=True,timeout=20)
            normalized=json.loads(result.stdout)
            for key in ['teamWeeks','playerWeeks','players','matchups','categoryRecord','transactions','tradeAnalysis','waiverAnalysis']:
                self.assertEqual(len(normalized[key]),len(season[key]))
            self.assertEqual(normalized['teamWeeks'][0]['stats'],season['teamWeeks'][0]['stats'])
            self.assertEqual(normalized['players'][0]['stats'],season['players'][0]['stats'])
            self.assertEqual(normalized['quality']['scope'],'official-team')
            export_all(season,tmp)
            text=(Path(tmp)/'fantasy_dashboard.html').read_text(encoding='utf-8')
            for script in re.findall(r'<script>([\s\S]*?)</script>',text):
                subprocess.run([NODE,'--check','--input-type=commonjs'],input=script,encoding='utf-8',capture_output=True,check=True,timeout=20)
                fixture=json.loads(re.search(r'<script id="data" type="application/json">([\s\S]*?)</script>',text).group(1))
                # Exercise generated offline player-chart selection without network or a browser.
                harness=r'''
const vm=require('node:vm'),assert=require('node:assert/strict');let raw='';
process.stdin.on('data',c=>raw+=c);process.stdin.on('end',()=>{
 const {script,payload}=JSON.parse(raw);
 class Element{constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.attrs={}}
 append(...children){this.children.push(...children)} replaceChildren(...children){this.children=children}
 setAttribute(k,v){this.attrs[k]=v} get value(){return this._value??this.children[0]?.value??this.textContent} set value(v){this._value=v}
 get style(){return this._style||(this._style={})}}
 const elements=Object.fromEntries(['data','content','nav','intro','warnings'].map(k=>[k,new Element(k)]));
 elements.data.textContent=JSON.stringify(payload);
 const document={getElementById:id=>elements[id],createElement:tag=>new Element(tag),querySelectorAll:()=>elements.nav.children};
 vm.runInNewContext(script,{document,console,Map,Math,String,JSON});
 elements.nav.children.find(n=>n.textContent==='球員走勢').onclick();
 const select=elements.content.children.find(n=>n.attrs['aria-label']==='走勢球員');
 assert.equal(select.children.length,2);select.value='999.p.2';select.onchange();
 const chart=elements.content.children.at(-1);assert.ok(chart.children[0].textContent.includes(' 0 '));
 console.log('offline player selector: PASS');
});'''
                subprocess.run([NODE,'-e',harness],input=json.dumps({'script':script,'payload':fixture}),encoding='utf-8',capture_output=True,check=True,timeout=20)

if __name__=='__main__':unittest.main()
