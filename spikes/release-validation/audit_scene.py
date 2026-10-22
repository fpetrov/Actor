#!/usr/bin/env python3
"""Read-only Unity scene graph comparison, independent of regenerated file IDs.

Only aggregate counts/hashes/booleans are written. The script never saves a
scene, restores a Git file or includes serialized component values in a report.
"""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import subprocess

LIMIT=16*1024*1024
DEFAULT_SCENE='Agents Arena/Assets/Scenes/MainHallMultiplayer.unity'
DEFAULT_ASSETS=('Agents Arena/Assets/Scenes/MainHall.unity','Agents Arena/Assets/World/SocialLayout.json')
COLLIDERS={'58','60','61','64','66','68','70','197','256'}


def bounded_path(repo: Path,value: str)->Path:
    raw=Path(value)
    if raw.is_absolute() or '..' in raw.parts:raise ValueError('Paths must be root-relative without parent traversal')
    path=(repo/raw).resolve()
    if not path.is_relative_to(repo):raise ValueError('Path escapes the repository')
    return path


def bounded_read(path: Path)->bytes:
    with path.open('rb') as stream:value=stream.read(LIMIT+1)
    if len(value)>LIMIT:raise ValueError('File exceeds the bounded scene audit size')
    return value


def git_bytes(repo: Path,*arguments: str)->bytes:
    result=subprocess.run(['git','-C',str(repo),*arguments],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
    if len(result.stdout)>LIMIT:raise ValueError('Git object exceeds the bounded scene audit size')
    return result.stdout


def parse_scene(text: str):
    if len(text.encode('utf-8'))>LIMIT:raise ValueError('Scene exceeds the bounded scene audit size')
    docs={}
    for match in re.finditer(r'^--- !u!(\d+) &(-?\d+)[^\n]*\n(.*?)(?=^--- !u!|\Z)',text,re.M|re.S):
        kind,anchor,body=match.groups();anchor=int(anchor)
        if anchor in docs:raise ValueError('Duplicate Unity file ID')
        docs[anchor]={'kind':kind,'body':body}
    if not docs or len(docs)>20000:raise ValueError('Invalid or oversized Unity document graph')
    objects={};transforms={};owners={};ids={0:'null'}
    for anchor,document in docs.items():
        body=document['body'];go=re.search(r'^  m_GameObject: \{fileID: (-?\d+)\}',body,re.M)
        if go:owners[anchor]=int(go[1])
        if document['kind']=='1':
            name=re.search(r'^  m_Name: (.*)$',body,re.M)
            if name is None:raise ValueError('GameObject name missing')
            objects[anchor]=name[1].rstrip()
        if document['kind']=='4':
            parent=re.search(r'^  m_Father: \{fileID: (-?\d+)\}',body,re.M)
            if go is None or parent is None:raise ValueError('Transform relation missing')
            transforms[anchor]=(int(go[1]),int(parent[1]))
    bygo={go:anchor for anchor,(go,_) in transforms.items()}
    sibling_counts=collections.Counter((transforms.get(bygo.get(go),(None,0))[1],name) for go,name in objects.items())
    def path(go,trail=()):
        if go not in objects:return ('external',go)
        if go in trail or len(trail)>128:raise ValueError('Cyclic or oversized transform hierarchy')
        parent=transforms.get(bygo.get(go),(None,0))[1]
        parentgo=transforms.get(parent,(None,None))[0]
        name=objects[go];discriminator=''
        if sibling_counts[(parent,name)]>1:
            # Exact transform text disambiguates regenerated same-named siblings.
            # A remaining ambiguity is rejected below rather than merged.
            discriminator='|'.join(value.rstrip() for value in re.findall(r'^  m_Local(?:Position|Rotation|Scale): (.*)$',docs[bygo[go]]['body'],re.M))
        return (*(path(parentgo,trail+(go,)) if parentgo in objects else ()), (name,discriminator))
    for anchor,document in docs.items():
        if document['kind']=='1':identity=('GameObject',path(anchor))
        elif anchor in owners:
            script=re.search(r'^  m_Script: .*?guid: ([0-9a-f]+)',document['body'],re.M)
            identity=(document['kind'],path(owners[anchor]),script[1] if script else '')
        else:identity=('scene',document['kind'])
        ids[anchor]=json.dumps(identity,separators=(',',':'))
    if len(set(ids.values()))!=len(ids):raise ValueError('Ambiguous component identities; refusing an unverifiable graph comparison')
    def normalize(document):
        lines=[line.rstrip() for line in document['body'].splitlines()];result=[];index=0
        def references(line):
            return re.sub(r'\{fileID: (-?\d+)\}',lambda match:'{ref: '+ids.get(int(match[1]),'external:'+match[1])+'}',line)
        while index<len(lines):
            line=references(lines[index]);result.append(line);index+=1
            if line.strip() in ('m_Children:','m_Roots:'):
                children=[]
                while index<len(lines) and re.match(r'  - \{fileID:',lines[index]):
                    children.append(references(lines[index]));index+=1
                result+=sorted(children)
        return '\n'.join(result)
    values={ids[anchor]:normalize(document) for anchor,document in docs.items()}
    geometry={ids[anchor] for anchor,document in docs.items() if document['kind'] in COLLIDERS or document['kind']=='4'}
    kinds=collections.Counter(document['kind'] for document in docs.values())
    return values,geometry,kinds


def compare_scenes(before: str,after: str):
    baseline,baseline_geometry,before_kinds=parse_scene(before)
    current,current_geometry,after_kinds=parse_scene(after)
    changes={key for key in baseline.keys()|current.keys() if baseline.get(key)!=current.get(key)}
    geometry_changes=changes&(baseline_geometry|current_geometry)
    return {'documents_before':len(baseline),'documents_after':len(current),'canonical_graph_equal':not changes,
        'changed_document_count':len(changes),'transforms_and_colliders_equal':not geometry_changes,'changed_geometry_count':len(geometry_changes),
        'transforms_before':before_kinds['4'],'transforms_after':after_kinds['4'],
        'colliders_before':sum(before_kinds[kind] for kind in COLLIDERS),'colliders_after':sum(after_kinds[kind] for kind in COLLIDERS)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--scene',default=DEFAULT_SCENE);parser.add_argument('--reference',default='HEAD')
    parser.add_argument('--asset',action='append',help='Additional root-relative asset to require byte-identical (defaults: MainHall and SocialLayout)')
    parser.add_argument('--output',default='build/release-validation/scene-semantic-audit.json')
    args=parser.parse_args(argv)
    try:
        repo=args.repo.resolve(strict=True)
        scene=bounded_path(repo,args.scene);output=bounded_path(repo,args.output)
        if output.suffix!='.json' or '.git' in Path(args.output).parts:raise ValueError('Output must be a JSON report outside Git metadata')
        if output==scene:raise ValueError('Output cannot overwrite the compared scene')
        assets=args.asset if args.asset is not None else DEFAULT_ASSETS
        asset_paths=[bounded_path(repo,asset) for asset in assets]
        if output in asset_paths:raise ValueError('Output cannot overwrite a compared asset')
        if output.exists() and json.loads(bounded_read(output)).get('schema')!='actor.scene-semantic-audit.v1':
            raise ValueError('Output cannot overwrite an unrelated existing JSON file')
        commit=git_bytes(repo,'rev-parse','--verify','--end-of-options',args.reference+'^{commit}').decode().strip()
        old=git_bytes(repo,'show',commit+':'+args.scene);new=bounded_read(scene)
        result=compare_scenes(old.decode(),new.decode())
        unchanged={}
        for asset,path in zip(assets,asset_paths):
            previous=git_bytes(repo,'show',commit+':'+asset);current=bounded_read(path)
            unchanged[asset]={'byte_identical':previous==current,'sha256':hashlib.sha256(current).hexdigest()}
        result.update(schema='actor.scene-semantic-audit.v1',scene=args.scene,reference_commit=commit,
            raw_scene_changed=old!=new,baseline_sha256=hashlib.sha256(old).hexdigest(),current_sha256=hashlib.sha256(new).hexdigest(),
            unchanged_assets=unchanged,
            method='Resolve local file IDs to unique hierarchy/component identities; disambiguate duplicate names by exact transform; ignore sibling/root order and YAML trailing whitespace; preserve external GUIDs and all component values.')
        result['passed']=result['canonical_graph_equal'] and all(value['byte_identical'] for value in unchanged.values())
        output.parent.mkdir(parents=True,exist_ok=True);temp=output.with_suffix(output.suffix+'.tmp')
        temp.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n');temp.replace(output)
        print(json.dumps({'passed':result['passed'],'documents':result['documents_after'],'changed_documents':result['changed_document_count'],'changed_geometry':result['changed_geometry_count']}))
        return 0 if result['passed'] else 1
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        parser.exit(2,'Scene audit failed safely: '+type(error).__name__+'\n')

if __name__=='__main__':raise SystemExit(main())
