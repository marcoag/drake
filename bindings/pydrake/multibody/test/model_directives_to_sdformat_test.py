import argparse
import xml.etree.ElementTree as ET
from xml.dom import minidom
import os
import unittest

from pydrake.multibody.parsing import (
    Parser,
)
from pydrake.multibody.plant import (
    MultibodyPlant,
)
from pydrake.multibody.tree import (
    BodyIndex,
    FrameIndex,
    ModelInstanceIndex,
    JointIndex,
)
from pydrake.common import GetDrakePath
from pydrake.common import temp_directory
from pydrake.common.test_utilities.meta import (
    ValueParameterizedTest,
    run_with_multiple_values,
)

from pydrake.multibody import (
    model_directives_to_sdformat
)
from pydrake.multibody.model_directives_to_sdformat import (
    convert_directives,
)

_SCOPE_DELIMITER = "::"


def _get_plant_aggregate(num_func, get_func, index_cls, model_instances=None):
    items = []
    for i in range(num_func()):
        item = get_func(index_cls(i))
        if model_instances is None or item.model_instance() in model_instances:
            items.append(item)
    return items


def get_model_instances(plant):
    # TODO(eric.cousineau): Hoist this somewhere?
    return _get_plant_aggregate(
        plant.num_model_instances, lambda x: x, ModelInstanceIndex
    )


def get_model_instances_names(plant):
    # TODO(eric.cousineau): Hoist this somewhere?
    return _get_plant_aggregate(
        plant.num_model_instances,
        plant.GetModelInstanceName,
        ModelInstanceIndex,
    )


def get_bodies(plant, model_instances=None):
    # TODO(eric.cousineau): Hoist this somewhere?
    return _get_plant_aggregate(
        plant.num_bodies, plant.get_body, BodyIndex, model_instances
    )


def get_frames(plant, model_instances=None):
    # TODO(eric.cousineau): Hoist this somewhere?
    return _get_plant_aggregate(
        plant.num_frames, plant.get_frame, FrameIndex, model_instances
    )


def get_frames_attached_to(plant, bodies):
    # TODO(eric.cousineau): Hoist this somewhere?
    frames = []
    for frame in get_frames(plant):
        if frame.body() in bodies:
            frames.append(frame)
    return frames


def get_joints(plant, model_instances=None):
    # TODO(eric.cousineau): Hoist this somewhere?
    return _get_plant_aggregate(
        plant.num_joints, plant.get_joint, JointIndex, model_instances
    )


def _strict_zip(*items):
    # TODO(eric.cousineau): Remove once `zip(*, strict=True)` is available:
    # https://www.python.org/dev/peps/pep-0618/
    # WARNING: Items should be *iterable*, meaning they can be iterated
    # over multiple times. This will not work for *iterators* that can be
    # exhausted.
    assert len(items) >= 1
    count = len(items[0])
    for i, x in enumerate(items):
        assert len(x) == count, f"len(items[{i}]) = {len(x)} != {count}"
    return zip(*items)


# Checks that body_a and body_b are effectively the same.
def assert_bodies_same(plant_a, context_a, body_a, plant_b, context_b, body_b):
    if body_a.name() != body_b.name():
        return False

    # Check that positions in the world frame reference are also the same.
    directives_body_transform = plant_a.EvalBodyPoseInWorld(context_a, body_a)
    sdformat_body_transform = plant_b.EvalBodyPoseInWorld(context_b, body_b)
    directives_body_transform.IsNearlyEqualTo(sdformat_body_transform, 1e-10)

    # Check frames attached to this body.
    frames_b = get_frames_attached_to(plant_b, [body_b])
    frames_a = get_frames_attached_to(plant_a, [body_a])

    # All frames created through model directives would have
    # been created when loading the SDFormat.
    if not all(
        frame_a.name() in [frame_b.name() for frame_b in frames_b]
        for frame_a in frames_a
    ):
        return False

    return True


# Compares if two lists of joints are similar. If they have the same
# child and parent, body name and the same type, we consider them the same.
def are_joints_same(joints_a, joints_b):
    for i in range(len(joints_a)):
        for j in range(len(joints_b)):
            if (
                joints_a[i].parent_body().name()
                == joints_b[j].parent_body().name()
                and joints_a[i].child_body().name()
                == joints_b[j].child_body().name()
                and joints_a[i].type_name() == joints_b[j].type_name()
            ):
                joints_a.pop(i)
                joints_b.pop(j)

    # All joints should have been verified.
    if len(joints_a) == 0 and len(joints_b) == 0:
        return True
    else:
        return False


class TestConvertModelDirectiveToSdformat(
    unittest.TestCase, metaclass=ValueParameterizedTest
):
    def setUp(self):
        self.dmd_test_path = (
            "bindings/pydrake/multibody/test/"
            "model_directives_to_sdformat_files"
        )
        self.temp_dir = temp_directory()

    @run_with_multiple_values([dict(name=name) for name in [
        "deep_frame",
        "deep_weld",
        "frame_attached_to_frame",
        "hidden_frame",
        "inject_frames",
        "scoped_frame_name",
        "weld_frames_from_models",
    ]])
    def test_through_plant_comparison(self, *, name):
        # Convert.
        file_path = f"{self.dmd_test_path}/{name}.dmd.yaml"
        sdformat_tree = convert_directives(
            model_directives=file_path).getroot()
        sdformat_result = minidom.parseString(
            ET.tostring(sdformat_tree)
        ).toprettyxml(indent="  ")

        # Load model directives.
        directives_plant = MultibodyPlant(time_step=0.0)
        model_dir_multibody = os.path.dirname(
            os.path.join(GetDrakePath(), "multibody/parsing/test/")
        )
        model_dir_bindings = os.path.dirname(
            os.path.join(GetDrakePath(), "bindings/pydrake/multibody/test/")
        )
        parser = Parser(plant=directives_plant)
        parser.package_map().PopulateFromFolder(model_dir_multibody)
        parser.package_map().PopulateFromFolder(model_dir_bindings)
        parser.AddModels(file_path)
        directives_plant.Finalize()

        # Load converted SDFormat.
        sdformat_plant = MultibodyPlant(time_step=0.0)
        sdformat_parser = Parser(sdformat_plant)
        sdformat_parser.package_map().PopulateFromFolder(model_dir_multibody)
        sdformat_parser.package_map().PopulateFromFolder(model_dir_bindings)
        sdformat_parser.AddModelsFromString(sdformat_result, "sdf")
        sdformat_plant.Finalize()

        # Compare plants.
        # The conversion process will create an extra top level
        # model instance. This can be avoided using the generated
        # world model with merge include.
        self.assertEqual(
            sdformat_plant.num_model_instances() - 1,
            directives_plant.num_model_instances(),
        )

        file_name = os.path.basename(
            model_directives_to_sdformat._remove_suffix(
                file_path, ".dmd.yaml"
            )
        )

        for i in range(2, directives_plant.num_model_instances()):
            model_scoped_name = (
                file_name
                + _SCOPE_DELIMITER
                + directives_plant.GetModelInstanceName(ModelInstanceIndex(i))
            )

            sdformat_model_instances = get_model_instances_names(
                sdformat_plant
            )
            model_found = False

            if model_scoped_name in sdformat_model_instances:
                model_found = True
                sdformat_model_index = sdformat_model_instances.index(
                    model_scoped_name
                )

            self.assertTrue(model_found)

            # Check Model Bodies and corresponding Frames.
            model_instance = ModelInstanceIndex(i)
            directives_bodies = get_bodies(directives_plant, [model_instance])
            directives_context = directives_plant.CreateDefaultContext()
            sdformat_intance_index = ModelInstanceIndex(sdformat_model_index)
            sdformat_bodies = get_bodies(
                sdformat_plant, [sdformat_intance_index]
            )
            sdformat_context = sdformat_plant.CreateDefaultContext()

            for sdformat_body, directives_body in _strict_zip(
                sdformat_bodies, directives_bodies
            ):
                self.assertTrue(
                    assert_bodies_same(
                        directives_plant,
                        directives_context,
                        directives_body,
                        sdformat_plant,
                        sdformat_context,
                        sdformat_body,
                    )
                )

            # Check Model Joints.
            self.assertTrue(
                are_joints_same(
                    get_joints(directives_plant, [model_instance]),
                    get_joints(sdformat_plant, [sdformat_intance_index]),
                )
            )

    @run_with_multiple_values([dict(name=name) for name in [
        "deep_child_frame_weld",
        "deep_child_weld",
        "default_free_body_pose",
        "default_joint_positions",
        "different_scopes_frame",
        "frame_same_as_base_frame",
        "frames_same_name",
        "implicit_hidden_base_frame",
        "not_directives_first",
        "something_not_directives",
        "weld_same_parent_child",
        "world_base_frame",
    ]])
    def test_error(self, *, name):
        file_path = f"{self.dmd_test_path}/errors/{name}.dmd.yaml"
        with open(f"{self.dmd_test_path}/errors/{name}.error_regex") as f:
            expected_message_regex = f.read().strip()
        with self.assertRaisesRegex(Exception, expected_message_regex):
            convert_directives(model_directives=file_path)

    def test_add_model_instance_add_directives(self):
        os.environ["ROS_PACKAGE_PATH"] = "bindings/pydrake/multibody"
        expected_sdf = """<?xml version="1.0" ?>
<sdf version="1.9">
  <model name="add_directives">
    <model name="model_instance">
      <include merge="true">
        <uri>package://model_directives_to_sdformat_files/hidden_frame.sdf</uri>
      </include>
    </model>
  </model>
</sdf>
"""
        expected_expanded_sdf = (
            """<?xml version="1.0" ?>
<sdf version="1.9">
  <model name="hidden_frame">
    <include>
      <name>simple_model</name>
      <uri>package://process_model_directives_test/simple_model.sdf</uri>
      <placement_frame>frame</placement_frame>
      <pose relative_to="top_level_model::top_injected_frame"/>
    </include>
    <model name="top_level_model">
      <include merge="true">
        <name>top_level_model</name>
        <uri>package://process_model_directives_test/simple_model.sdf</uri>
      </include>
      <frame name="top_injected_frame" attached_to="base">
        <pose degrees="true">1.0 2.0 3.0   10.0 20.0 30.0</pose>
      </frame>
    </model>
    <joint name="top_level_model__top_injected_frame__to__simple_model__"""
            """frame__weld_joint" type="fixed">
      <parent>top_level_model::top_injected_frame</parent>
      <child>simple_model::frame</child>
    </joint>
  </model>
</sdf>
"""
        )
        model_directives_to_sdformat._main(
            [
                "-m",
                "bindings/pydrake/multibody/test/"
                "model_directives_to_sdformat_files/"
                "add_directives.dmd.yaml",
                "--expand-included",
                "-o",
                self.temp_dir,
            ]
        )

        self.assertEqual(
            expected_sdf,
            open(os.path.join(self.temp_dir, "add_directives.sdf")).read(),
        )
        self.assertEqual(
            expected_expanded_sdf,
            open(
                os.path.join(
                    self.temp_dir,
                    "model_directives_to_sdformat_files/hidden_frame.sdf",
                )
            ).read(),
        )

    def test_resulting_xml(self):
        expected_xml = (
            """<?xml version="1.0" ?>
<sdf version="1.9">
  <model name="inject_frames">
    <model name="top_level_model">
      <include merge="true">
        <name>top_level_model</name>
        <uri>package://process_model_directives_test/simple_model.sdf</uri>
      </include>
      <frame name="top_injected_frame" attached_to="base">
        <pose degrees="true">1.0 2.0 3.0   10.0 20.0 30.0</pose>
      </frame>
    </model>
    <model name="mid_level_model" placement_frame="base">
      <pose relative_to="top_level_model::top_injected_frame"/>
      <include merge="true">
        <name>mid_level_model</name>
        <uri>package://process_model_directives_test/simple_model.sdf</uri>
      </include>
      <frame name="mid_injected_frame" attached_to="base">
        <pose degrees="true">1.0 2.0 3.0   0 0 0</pose>
      </frame>
    </model>
    <joint name="top_level_model__top_injected_frame__to__mid_level_model__"""
            """base__weld_joint" type="fixed">
      <parent>top_level_model::top_injected_frame</parent>
      <child>mid_level_model::base</child>
    </joint>
    <include>
      <name>bottom_level_model</name>
      <uri>package://process_model_directives_test/simple_model.sdf</uri>
      <placement_frame>base</placement_frame>
      <pose relative_to="mid_level_model::mid_injected_frame"/>
    </include>
    <joint name="mid_level_model__mid_injected_frame__to__bottom_level_"""
            """model__base__weld_joint" type="fixed">
      <parent>mid_level_model::mid_injected_frame</parent>
      <child>bottom_level_model::base</child>
    </joint>
  </model>
</sdf>
"""
        )
        file_path = f"{self.dmd_test_path}/inject_frames.dmd.yaml"
        result = convert_directives(model_directives=file_path).getroot()
        xmlstr = minidom.parseString(ET.tostring(result)).toprettyxml(
            indent="  "
        )
        self.assertEqual(expected_xml, xmlstr)

    def test_error_wrong_file_extension(self):
        with self.assertRaisesRegex(Exception, "determine file format"):
            file_path = "frame_same_as_base_frame.not_valid"
            convert_directives(model_directives=file_path)
