import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC=importlib.util.spec_from_file_location('scene_audit',Path(__file__).with_name('audit_scene.py'))
audit=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(audit)


def scene(go=10,transform=11,collider=12,position='1',width='2',layer='0'):
    return f'''%YAML 1.1
--- !u!1 &{go}
GameObject:
  m_Component:
  - component: {{fileID: {transform}}}
  - component: {{fileID: {collider}}}
  m_Name: Floor
  m_Layer: {layer}
--- !u!4 &{transform}
Transform:
  m_GameObject: {{fileID: {go}}}
  m_LocalRotation: {{x: 0, y: 0, z: 0, w: 1}}
  m_LocalPosition: {{x: {position}, y: 0, z: 0}}
  m_LocalScale: {{x: 1, y: 1, z: 1}}
  m_Children: []
  m_Father: {{fileID: 0}}
--- !u!61 &{collider}
BoxCollider2D:
  m_GameObject: {{fileID: {go}}}
  m_Enabled: 1
  m_Size: {{x: {width}, y: 1}}
'''


class SemanticSceneTests(unittest.TestCase):
    def test_regenerated_local_ids_do_not_change_graph(self):
        result=audit.compare_scenes(scene(),scene(900,850,800))
        self.assertTrue(result['canonical_graph_equal']);self.assertEqual(result['documents_after'],3)
        self.assertEqual(result['colliders_after'],1);self.assertEqual(result['transforms_after'],1)
    def test_order_and_insignificant_trailing_space_do_not_change_graph(self):
        original=scene();parts=original.split('--- !u!');reordered=parts[0]+''.join('--- !u!'+value for value in reversed(parts[1:]))
        self.assertTrue(audit.compare_scenes(original,reordered.replace('m_Name: Floor','m_Name: Floor  '))['canonical_graph_equal'])
    def test_collider_size_transform_and_layer_change_never_pass(self):
        for changed,geometry_change in ((scene(width='3'),True),(scene(position='4'),True),(scene(layer='8'),False)):
            with self.subTest(geometry=geometry_change):
                result=audit.compare_scenes(scene(),changed);self.assertFalse(result['canonical_graph_equal'])
                self.assertEqual(not result['transforms_and_colliders_equal'],geometry_change)
    def test_external_asset_guid_is_preserved(self):
        first=scene()+'--- !u!212 &13\nSpriteRenderer:\n  m_GameObject: {fileID: 10}\n  m_Sprite: {fileID: 21300000, guid: '+('a'*32)+', type: 3}\n'
        self.assertFalse(audit.compare_scenes(first,first.replace('a'*32,'b'*32))['canonical_graph_equal'])
    def test_ambiguous_component_graph_is_rejected_instead_of_merged(self):
        duplicate=scene()+'--- !u!61 &14\nBoxCollider2D:\n  m_GameObject: {fileID: 10}\n  m_Size: {x: 5, y: 5}\n'
        with self.assertRaisesRegex(ValueError,'Ambiguous'):audit.parse_scene(duplicate)
    def test_cycle_duplicate_anchor_and_empty_are_rejected(self):
        for invalid in ('',scene().replace('m_Father: {fileID: 0}','m_Father: {fileID: 11}'),scene()+'--- !u!61 &12\nBoxCollider2D:\n'):
            with self.subTest(invalid=invalid[:20]):
                with self.assertRaises(ValueError):audit.parse_scene(invalid)
    def test_paths_are_relative_contained_and_symlink_safe(self):
        with tempfile.TemporaryDirectory() as folder,tempfile.TemporaryDirectory() as outside:
            root=Path(folder).resolve();(root/'escape').symlink_to(Path(outside),target_is_directory=True)
            self.assertEqual(audit.bounded_path(root,'build/report.json'),root/'build/report.json')
            for invalid in ('../report.json','/tmp/report.json','escape/report.json'):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(ValueError):audit.bounded_path(root,invalid)

if __name__=='__main__':unittest.main()
