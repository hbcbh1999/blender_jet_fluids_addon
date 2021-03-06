
import os
import numpy
import struct

import bpy

from . import pyjet
from . import bake


class JetFluidBakeParticles(bpy.types.Operator):
    bl_idname = "jet_fluid.bake_particles"
    bl_label = "Bake Particles"
    bl_options = {'REGISTER'}

    def find_emitters_and_colliders(self):
        emitters = []
        colliders = []
        obj_names = {obj.name for obj in bpy.data.objects}
        for obj_name in obj_names:
            obj = bpy.data.objects.get(obj_name)
            if not obj:
                continue
            if obj.jet_fluid.is_active:
                if obj.jet_fluid.object_type == 'EMITTER':
                    emitters.append(obj)
                elif obj.jet_fluid.object_type == 'COLLIDER':
                    colliders.append(obj)
        return emitters, colliders

    def simulate(self, offset=0, particles_colors=[]):
        print('EXECUTE START')
        solv = self.solver
        resolution_x, resolution_y, resolution_z, origin_x, origin_y, origin_z, domain_size_x, grid_spacing = bake.calc_res(self, self.domain, type='MESH')
        jet = self.domain.jet_fluid
        create_mesh = jet.create_mesh
        create_particles = jet.create_particles
        show_particles = jet.show_particles
        jet.create_mesh = False
        jet.create_particles = False
        jet.show_particles = False
        current_frame = self.context.scene.frame_current
        print('grid')
        folder = bpy.path.abspath(self.domain.jet_fluid.cache_folder)
        while self.frame.index + offset <= self.frame_end:
            print('frame start', self.frame.index + offset)
            self.context.scene.frame_set(self.frame.index + offset)
            for emitter in self.emitters:
                jet_emmiter = self.jet_emitters_dict.get(emitter.name, None)
                if jet_emmiter:
                    vel = emitter.jet_fluid.velocity
                    jet_emmiter.initialVelocity = vel[0], vel[2], vel[1]
                    jet_emmiter.isOneShot = emitter.jet_fluid.one_shot
            file_path = '{}particles_{}.bin'.format(
                bpy.path.abspath(self.domain.jet_fluid.cache_folder),
                self.frame.index + offset
            )
            solv.viscosityCoefficient = self.domain.jet_fluid.viscosity
            print('solver update start')
            solv.update(self.frame)
            print('solver update end')
            print('start save particles')
            positions = numpy.array(solv.particleSystemData.positions, copy=False)
            velocities = numpy.array(solv.particleSystemData.velocities, copy=False)
            forces = numpy.array(solv.particleSystemData.forces, copy=False)
            print('numpy convert')
            bin_data = bytearray()
            vertices_count = len(positions)
            bin_data += struct.pack('I', vertices_count)
            print('save particles colors')
            par_color = self.domain.jet_fluid.particles_color
            colors_count = len(particles_colors)
            for i in range(vertices_count - colors_count):
                particles_colors.append((par_color[0], par_color[1], par_color[2]))
            print('start save position and velocity')
            for vert_index in range(vertices_count):
                bin_data.extend(struct.pack('3f', *positions[vert_index]))
                bin_data.extend(struct.pack('3f', *velocities[vert_index]))
                bin_data.extend(struct.pack('3f', *forces[vert_index]))
                bin_data.extend(struct.pack('3f', *particles_colors[vert_index]))
            file = open(file_path, 'wb')
            file.write(bin_data)
            file.close()
            print('end save particles')
            self.frame.advance()
        jet.create_mesh = create_mesh
        jet.create_particles = create_particles
        jet.show_particles = show_particles
        self.context.scene.frame_set(current_frame)
        return {'FINISHED'}

    def execute(self, context):
        print('INVOKE START')
        self.context = context
        pyjet.Logging.mute()
        obj = context.object
        if not obj.jet_fluid.cache_folder:
            self.report({'WARNING'}, 'Cache Folder not Specified!')
            return {'FINISHED'}
        print('create solver')
        resolution_x, resolution_y, resolution_z, origin_x, origin_y, origin_z, domain_size_x, _ = bake.calc_res(self, obj)
        solver = bake.solvers[obj.jet_fluid.solver_type](
            resolution=(resolution_x, resolution_z, resolution_y),
            gridOrigin=(origin_x, origin_z, origin_y),
            domainSizeX=domain_size_x
        )
        print('set solver props')
        solver.maxCfl = obj.jet_fluid.max_cfl
        solver.advectionSolver = bake.advection_solvers[obj.jet_fluid.advection_solver_type]()
        solver.diffusionSolver = bake.diffusion_solvers[obj.jet_fluid.diffusion_solver_type]()
        solver.pressureSolver = bake.pressure_solvers[obj.jet_fluid.pressure_solver_type]()
        solver.useCompressedLinearSystem = obj.jet_fluid.compressed_linear_system
        solver.isUsingFixedSubTimeSteps = obj.jet_fluid.fixed_substeps
        solver.numberOfFixedSubTimeSteps = obj.jet_fluid.fixed_substeps_count
        bake.set_closed_domain_boundary_flag(solver, obj)
        solver.viscosityCoefficient = obj.jet_fluid.viscosity
        grav = obj.jet_fluid.gravity
        solver.gravity = grav[0], grav[2], grav[1]
        if obj.jet_fluid.use_scene_fps:
            frame = pyjet.Frame(0, 1.0 / context.scene.render.fps)
        else:
            frame = pyjet.Frame(0, 1.0 / obj.jet_fluid.fps)
        self.solver = solver
        self.frame = frame
        self.frame_end = context.scene.frame_end
        print('create others objects')
        for frame_index in range(0, self.frame_end):
            file_path = '{}particles_{}.bin'.format(
                bpy.path.abspath(self.domain.jet_fluid.cache_folder),
                frame_index
            )
            if os.path.exists(file_path):
                print('skip frame', frame_index)
                continue
            else:
                if frame_index == 0:
                    emitters, colliders = self.find_emitters_and_colliders()
                    jet_emitters = []
                    self.jet_emitters_dict = {}
                    print('create emitters')
                    for emitter_object in emitters:
                        print('create mesh')
                        triangle_mesh = bake.get_triangle_mesh(context, emitter_object, solver, obj)
                        init_vel = emitter_object.jet_fluid.velocity
                        print('create particle emitter')
                        emitter = pyjet.VolumeParticleEmitter3(
                            implicitSurface=triangle_mesh,
                            spacing=self.domain_max_size / (obj.jet_fluid.resolution * emitter_object.jet_fluid.particles_count),
                            isOneShot=emitter_object.jet_fluid.one_shot,
                            initialVelocity=[init_vel[0], init_vel[2], init_vel[1]],
                            jitter=emitter_object.jet_fluid.emitter_jitter,
                            allowOverlapping=emitter_object.jet_fluid.allow_overlapping,
                            seed=emitter_object.jet_fluid.emitter_seed,
                            maxNumberOfParticles=emitter_object.jet_fluid.max_number_of_particles
                        )
                        self.jet_emitters_dict[emitter_object.name] = emitter
                        jet_emitters.append(emitter)
                    print('create particle emitter set')
                    self.emitters = emitters
                    emitter_set = pyjet.ParticleEmitterSet3(emitters=jet_emitters)
                    solver.particleEmitter = emitter_set
                    # set colliders
                    print('create colliders')
                    jet_colliders = []
                    for collider_object in colliders:
                        print('create collider mesh')
                        triangle_mesh = bake.get_triangle_mesh(context, collider_object, solver, obj)
                        jet_colliders.append(triangle_mesh)
                    if jet_colliders:
                        print('create collider set')
                        collider_surface = pyjet.SurfaceSet3(others=jet_colliders)
                        collider = pyjet.RigidBodyCollider3(surface=collider_surface)
                        solver.collider = collider
                    # simulate
                    self.simulate()
                    break
                else:
                    last_frame = frame_index - 1
                    file_path = '{}particles_{}.bin'.format(
                        bpy.path.abspath(self.domain.jet_fluid.cache_folder),
                        last_frame
                    )
                    emitters, colliders = self.find_emitters_and_colliders()
                    jet_emitters = []
                    self.jet_emitters_dict = {}
                    for emitter_object in emitters:
                        if not emitter_object.jet_fluid.one_shot:
                            triangle_mesh = bake.get_triangle_mesh(context, emitter_object, solver, obj)
                            init_vel = emitter_object.jet_fluid.velocity
                            emitter = pyjet.VolumeParticleEmitter3(
                                implicitSurface=triangle_mesh,
                                spacing=self.domain_max_size / (obj.jet_fluid.resolution * emitter_object.jet_fluid.particles_count),
                                isOneShot=emitter_object.jet_fluid.one_shot,
                                initialVelocity=[init_vel[0], init_vel[2], init_vel[1]]
                            )
                            self.jet_emitters_dict[emitter_object.name] = emitter
                            jet_emitters.append(emitter)
                    self.emitters = emitters
                    emitter_set = pyjet.ParticleEmitterSet3(emitters=jet_emitters)
                    solver.particleEmitter = emitter_set
                    # set colliders
                    jet_colliders = []
                    for collider_object in colliders:
                        triangle_mesh = bake.get_triangle_mesh(context, collider_object, solver, obj)
                        jet_colliders.append(triangle_mesh)
                    if jet_colliders:
                        collider_surface = pyjet.SurfaceSet3(others=jet_colliders)
                        collider = pyjet.RigidBodyCollider3(surface=collider_surface)
                        solver.collider = collider
                    # resume particles
                    pos, vel, forc, colors = bake.read_particles(file_path)
                    solver.particleSystemData.addParticles(pos, vel, forc)
                    self.simulate(offset=last_frame, particles_colors=colors)
                    break
        return {'FINISHED'}

    def invoke(self, context, event):
        self.execute(context)
        return {'FINISHED'}


def register():
    bpy.utils.register_class(JetFluidBakeParticles)


def unregister():
    bpy.utils.unregister_class(JetFluidBakeParticles)
