
import struct
import numpy

import bpy

from . import pyjet


solvers = {
    'APIC': pyjet.ApicSolver3,
    'PIC': pyjet.PicSolver3,
    'FLIP': pyjet.FlipSolver3
}


def get_triangle_mesh(context, source, solver):
    selected_objects_name = [o.name for o in context.selected_objects]
    active_object_name = context.scene.objects.active.name
    bpy.ops.object.select_all(action='DESELECT')
    source.select = True
    bpy.ops.object.duplicate()
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    obj = context.selected_objects[0]
    mesh = obj.data
    context.scene.objects.active = obj
    bpy.ops.object.convert(target='MESH')
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.reveal()
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.quads_convert_to_tris(quad_method='BEAUTY', ngon_method='BEAUTY')
    bpy.ops.object.mode_set(mode='OBJECT')
    triangle_mesh = pyjet.TriangleMesh3(
        points=[[v.co.x, v.co.z, v.co.y] for v in mesh.vertices],
        pointIndices=[[p.vertices[0], p.vertices[2], p.vertices[1]] for p in mesh.polygons]
    )
    imp_triangle_mesh = pyjet.ImplicitTriangleMesh3(mesh=triangle_mesh, resolutionX=solver.resolution.x, margin=0)
    bpy.data.objects.remove(obj)
    bpy.data.meshes.remove(mesh)
    for obj_name in selected_objects_name:
        bpy.data.objects[obj_name].select = True
    bpy.context.scene.objects.active = bpy.data.objects[active_object_name]
    return imp_triangle_mesh


class JetFluidBake(bpy.types.Operator):
    bl_idname = "jet_fluid.bake"
    bl_label = "Bake"
    bl_options = {'REGISTER'}

    def execute(self, context):
        solv = self.solver
        resolution_x, resolution_y, resolution_z, origin_x, origin_y, origin_z, domain_size_x, grid_spacing = self.calc_res(self.domain, type='MESH')
        grid = pyjet.CellCenteredScalarGrid3(
            resolution=(resolution_x, resolution_z, resolution_y),
            gridOrigin=(origin_x, origin_z, origin_y),
            domainSizeX=self.domain_size_x
        )
        while self.frame.index <= self.frame_end:
            solv.update(self.frame)
            positions = numpy.array(solv.particleSystemData.positions, copy=False)
            velocities = numpy.array(solv.particleSystemData.velocities, copy=False)
            bin_data = b''
            bin_data += struct.pack('I', len(positions))
            for position, velocity in zip(positions, velocities):
                bin_position = struct.pack('3f', *position)
                bin_data += bin_position
                bin_velocity = struct.pack('3f', *velocity)
                bin_data += bin_velocity
            file_path = '{}particles_{}.bin'.format(self.domain.jet_fluid.cache_folder, self.frame.index)
            file = open(file_path, 'wb')
            file.write(bin_data)
            file.close()
            converter = pyjet.SphPointsToImplicit3(2.0 * solv.gridSpacing.x, 0.5)
            converter.convert(positions.tolist(), grid)
            surface_mesh = pyjet.marchingCubes(
                grid,
                (solv.gridSpacing.x, solv.gridSpacing.y, solv.gridSpacing.z),
                (0, 0, 0),
                0.0,
                pyjet.DIRECTION_ALL
            )

            coef = self.domain.jet_fluid.resolution / self.domain.jet_fluid.resolution_mesh
            bin_mesh_data = b''
            points_count = surface_mesh.numberOfPoints()
            bin_mesh_data += struct.pack('I', points_count)
            for point_index in range(points_count):
                point = surface_mesh.point(point_index)
                bin_mesh_data += struct.pack('3f', point.x * coef, point.y * coef, point.z * coef)

            triangles_count = surface_mesh.numberOfTriangles()
            bin_mesh_data += struct.pack('I', triangles_count)
            for triangle_index in range(triangles_count):
                tris = surface_mesh.pointIndex(triangle_index)
                bin_mesh_data += struct.pack('3I', tris.x, tris.y, tris.z)

            file_path = '{}mesh_{}.bin'.format(self.domain.jet_fluid.cache_folder, self.frame.index)
            file = open(file_path, 'wb')
            file.write(bin_mesh_data)
            file.close()
            self.frame.advance()
        return {'FINISHED'}

    def calc_res(self, obj, type='FLUID'):
        self.domain = obj
        domain_size_x = obj.bound_box[6][0] * obj.scale[0] - obj.bound_box[0][0] * obj.scale[0]
        domain_size_y = obj.bound_box[6][1] * obj.scale[1] - obj.bound_box[0][1] * obj.scale[1]
        domain_size_z = obj.bound_box[6][2] * obj.scale[2] - obj.bound_box[0][2] * obj.scale[2]
        domain_sizes = [
            domain_size_x,
            domain_size_y,
            domain_size_z
        ]
        self.domain_size_x = domain_size_x
        if type == 'FLUID':
            resolution = obj.jet_fluid.resolution
            grid_spacing = (0, 0, 0)
        elif type == 'MESH':
            resolution = obj.jet_fluid.resolution_mesh
            fluid_res = obj.jet_fluid.resolution
            grid_spacing_x = resolution
            grid_spacing_y = resolution
            grid_spacing_z = resolution
            grid_spacing = (grid_spacing_x, grid_spacing_z, grid_spacing_y)
        self.domain_max_size = max(domain_sizes)
        resolution_x = int((domain_size_x / self.domain_max_size) * resolution)
        resolution_y = int((domain_size_y / self.domain_max_size) * resolution)
        resolution_z = int((domain_size_z / self.domain_max_size) * resolution)
        origin_x = obj.bound_box[0][0] * obj.scale[0] + obj.location[0]
        origin_y = obj.bound_box[0][1] * obj.scale[1] + obj.location[1]
        origin_z = obj.bound_box[0][2] * obj.scale[2] + obj.location[2]
        return resolution_x, resolution_y, resolution_z, origin_x, origin_y, origin_z, domain_size_x, grid_spacing

    def invoke(self, context, event):
        pyjet.Logging.mute()
        obj = context.scene.objects.active
        resolution_x, resolution_y, resolution_z, origin_x, origin_y, origin_z, domain_size_x, _ = self.calc_res(obj)
        solver = solvers[obj.jet_fluid.solver_type](
            resolution=(resolution_x, resolution_z, resolution_y),
            gridOrigin=(origin_x, origin_z, origin_y),
            domainSizeX=domain_size_x
        )
        solver.useCompressedLinearSystem = True
        solver.viscosityCoefficient = obj.jet_fluid.viscosity
        triangle_mesh = get_triangle_mesh(context, bpy.data.objects[obj.jet_fluid.emitter], solver)
        emitter = pyjet.VolumeParticleEmitter3(
            implicitSurface=triangle_mesh,
            spacing=self.domain_max_size / (obj.jet_fluid.resolution * obj.jet_fluid.particles_count),
            isOneShot=obj.jet_fluid.one_shot,
            initialVel=[v for v in obj.jet_fluid.velocity]
        )
        solver.particleEmitter = emitter
        collider_name = obj.jet_fluid.collider
        if collider_name:
            triangle_mesh = get_triangle_mesh(context, bpy.data.objects[obj.jet_fluid.collider], solver)
            collider = pyjet.RigidBodyCollider3(surface=triangle_mesh)
            solver.collider = collider

        frame = pyjet.Frame(0, 1.0 / context.scene.render.fps)
        self.solver = solver
        self.frame = frame
        self.frame_end = context.scene.frame_end
        self.execute(context)
        return {'RUNNING_MODAL'}


def register():
    bpy.utils.register_class(JetFluidBake)


def unregister():
    bpy.utils.unregister_class(JetFluidBake)
