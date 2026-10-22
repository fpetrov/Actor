"""Checks generated production content, not a snapshot of the drawing implementation."""
import hashlib, json, math, struct, unittest, wave
from pathlib import Path
from xml.etree import ElementTree as ET
from PIL import Image
ROOT=Path(__file__).resolve().parents[2]
ART=ROOT/'art/atmospheric'
OUT=ROOT/'Agents Arena/Assets/Art/Atmospheric/Generated'
class AssetTests(unittest.TestCase):
    def test_manifest_matches_original_assets_and_layout(self):
        m=json.loads((ART/'manifest.json').read_text())
        self.assertTrue(m['original']); self.assertEqual(m['kill_duration_ms'],640)
        for name,digest in m['files'].items():
            self.assertEqual(hashlib.sha256((OUT/name).read_bytes()).hexdigest(),digest,name)
        layout=json.loads((ROOT/'Agents Arena/Assets/World/SocialLayout.json').read_text())
        self.assertEqual(hashlib.sha256(json.dumps(layout,sort_keys=True,separators=(',',':')).encode()).hexdigest(),m['layout_semantic_sha256'])
    def test_six_directions_have_eight_distinct_walk_frames_and_transparent_padding(self):
        for direction in ('down','up','left','right','downright','upright'):
            im=Image.open(OUT/f'surveyor-{direction}.png').convert('RGBA')
            self.assertEqual(im.size,(1920,350))
            frames=[im.crop((i*240,0,(i+1)*240,350)) for i in range(8)]
            self.assertGreaterEqual(len({f.tobytes() for f in frames}),5)
            for f in frames:
                self.assertIsNone(f.crop((0,0,15,350)).getbbox())
                self.assertIsNone(f.crop((225,0,240,350)).getbbox())
                self.assertGreater(sum(1 for r,g,b,a in f.getdata() if a>200 and r>100 and b>100 and g<4),1000)
    def test_corpse_has_three_original_shapes_and_is_wider_than_tall(self):
        frames=[Image.open(OUT/f'fold-{i}.png').convert('RGBA') for i in range(3)]
        self.assertEqual(len({f.tobytes() for f in frames}),3)
        for f in frames:
            self.assertEqual(f.size,(240,150)); self.assertGreater(f.getbbox()[2]-f.getbbox()[0],f.getbbox()[3]-f.getbbox()[1])
    def test_all_svg_sources_are_self_contained_editable_vectors(self):
        files=list((ART/'source').glob('*.svg')); self.assertEqual(len(files),28)
        for f in files:
            root=ET.fromstring(f.read_text())
            self.assertFalse(any(e.tag.endswith(('image','script','foreignObject')) for e in root.iter()))
    def test_wall_landmarks_cannot_be_mistaken_for_walk_through_floor_obstacles(self):
        floor=json.loads((ROOT/'Agents Arena/Assets/World/SocialLayout.json').read_text())['floors']
        places=json.loads((ART/'placements.json').read_text())['landmarks']
        def cross(a,b,c): return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        def inside(p,poly):
            result=False
            for a,b in zip(poly,poly[1:]+poly[:1]):
                if (a[1]>p[1])!=(b[1]>p[1]) and p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0]: result=not result
            return result
        for place in places:
            w,h=place['width']/100,place['height']/100
            x,y=place['x']-place['pivot_x']*w,place['y']-place['pivot_y']*h
            box=[(x,y),(x+w,y),(x+w,y+h),(x,y+h)]
            for f in floor:
                poly=[(p['x'],p['y']) for p in f['physical_polygon']]
                self.assertFalse(any(inside(p,poly) for p in box),place['sprite'])
                self.assertFalse(any(x<=px<=x+w and y<=py<=y+h for px,py in poly),place['sprite'])
                for a,b in zip(box,box[1:]+box[:1]):
                    for c,d in zip(poly,poly[1:]+poly[:1]):
                        self.assertFalse(cross(a,b,c)*cross(a,b,d)<0 and cross(c,d,a)*cross(c,d,b)<0,place['sprite'])
    def test_audio_is_finite_short_non_clipped_mono_pcm(self):
        files=list(OUT.glob('*.wav')); self.assertEqual(len(files),11)
        for f in files:
            with wave.open(str(f),'rb') as audio:
                self.assertEqual((audio.getnchannels(),audio.getsampwidth(),audio.getframerate()),(1,2,48000))
                data=audio.readframes(audio.getnframes()); duration=audio.getnframes()/48000
            values=struct.unpack('<'+'h'*(len(data)//2),data)
            self.assertLessEqual(duration,.36); self.assertGreater(duration,.02)
            self.assertLess(max(abs(x) for x in values),20000)
            self.assertGreater(sum(x*x for x in values),100000)
            self.assertLess(abs(values[-1]),3)
    def test_floor_decorations_are_clipped_to_actual_floor(self):
        root=ET.fromstring((ART/'source/station-floor.svg').read_text())
        ns={'s':'http://www.w3.org/2000/svg'}
        clip=root.find('s:defs/s:clipPath',ns); self.assertIsNotNone(clip)
        layout=json.loads((ROOT/'Agents Arena/Assets/World/SocialLayout.json').read_text())
        self.assertEqual(len(list(clip)),len(layout['floors']))
        self.assertEqual(root.find("s:g[@clip-path='url(#floor)']",ns).attrib['clip-path'],'url(#floor)')
if __name__=='__main__': unittest.main()
