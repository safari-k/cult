from __future__ import division
import itertools
import struct

import panda3d.core as core
from direct.showbase.DirectObject import DirectObject
from direct.task.TaskManagerGlobal import taskMgr
from direct.showbase.MessengerGlobal import messenger
from direct.directnotify.DirectNotifyGlobal import directNotify

import world


notify = directNotify.newCategory('geometry')


class GeomBuilder(object):
    def __init__(self, name, master):
        self.name = name
        self.master = master
        self.primitive = core.GeomTriangles(core.Geom.UHStatic)
        self.primitive.setIndexType(core.Geom.NTUint32)
        self.indices = []

    def add_block(self, x, y, form, hidden):
        if hidden:
            form = world.Block.FORMS['Hidden']
        else:
            form = form
        offset = self.master.index_offset(x, y, form)
        self.indices.extend([i + offset for i in form.indices])

    def build(self):
        array = self.primitive.modifyVertices()
        fmt = array.getArrayFormat().getFormatString()
        data = struct.pack(fmt * len(self.indices), *self.indices)
        array.modifyHandle().setData(data)
        geom = core.Geom(self.master.vertexdata)
        geom.addPrimitive(self.primitive)
        gnode = core.GeomNode('{}-node'.format(self.name))
        gnode.addGeom(geom)
        return gnode


class MasterChunk(object):
    def __init__(self, size, forms):
        notify.info('Building the Master Chunk')
        self.size = size
        self.vertexformat = core.GeomVertexFormat.getV3n3t2()
        self.vertexdata = core.GeomVertexData('MasterChunk', self.vertexformat, core.Geom.UHStatic)

        vertex_writer = core.GeomVertexWriter(self.vertexdata, 'vertex')
        normal_writer = core.GeomVertexWriter(self.vertexdata, 'normal')
        texcoord_writer = core.GeomVertexWriter(self.vertexdata, 'texcoord')

        self.index_offsets = {}

        i = 0
        for x, y in itertools.product(range(self.size), range(self.size)):
            for form in forms:
                self.index_offsets[(x, y, form.name)] = i
                for v, n, t in form.vertices:
                    vertex_writer.addData3f(v[0] + x, v[1] + y, v[2])
                    normal_writer.addData3f(*n)
                    texcoord_writer.addData2f(*t)
                i += form.num_vertices

        notify.info('Master Chunk building complete')

    def index_offset(self, x, y, form):
        return self.index_offsets[(x, y, form.name)]


class Slice(core.NodePath):
    chunk_size = 32
    master = None

    def __init__(self, world, z):
        core.NodePath.__init__(self, 'slice-{}'.format(z))

        if Slice.master is None:
            Slice.master = MasterChunk(self.chunk_size, world.forms.values())

        self.world = world
        self.z = z

        self.chunks = {}
        self.hidden_chunks = {}

        self.setPos(0, 0, z)

        self.substances = [None] + [
            loader.loadTexture('media/textures/{}.png'.format(name),
                               anisotropicDegree=16,
                               minfilter=core.Texture.FTLinearMipmapLinear)
            for name in ['dirt', 'stone']
        ]

        self.hiddentexture = loader.loadTexture('media/textures/hidden.png',
                                                anisotropicDegree=16,
                                                minfilter=core.Texture.FTLinearMipmapLinear)
        self.hiddentexture.setWrapU(core.Texture.WMClamp)
        self.hiddentexture.setWrapV(core.Texture.WMClamp)

        self.show_stale_chunks = False

        self.updates = set()
        self.first_update_done = False

        self.task = taskMgr.add(self.perform_updates, 'Slice update')

        messenger.accept('console-command', self, self.command)

    def command(self, args):
        args = list(args)
        cmd = args.pop(0)
        if cmd == 'show-stale-chunks':
            self.show_stale_chunks = not self.show_stale_chunks

    def update_all(self):
        for cx, cy in itertools.product(range(self.world.width // self.chunk_size), range(self.world.height // self.chunk_size)):
            self.build_chunk(cx, cy)

    def first_update(self):
        if not self.first_update_done:
            self.update_all()
            self.first_update_done = True


    def destroy(self):
        messenger.ignoreAll(self)
        self.detachNode()
        taskMgr.remove(self.task)

    def build_chunk(self, cx, cy):
        hbuilder = GeomBuilder('slice-{}-hidden'.format(self.z), self.master)
        builders = {}

        chunk_size = self.chunk_size
        block_getter = self.world.get_raw

        for x, y in itertools.product(range(chunk_size), range(chunk_size)):
            form, substance, hidden = block_getter(cx * chunk_size + x, cy * chunk_size + y, self.z)
            if not substance:
                continue

            if hidden:
                builder = hbuilder
            else:
                if substance not in builders:
                    builders[substance] = GeomBuilder('slice-{}-geom-{}'.format(self.z, substance), self.master)
                builder = builders[substance]

            builder.add_block(x, y, form, hidden)

        hide = True
        old = self.chunks.get((cx, cy))
        oldh = self.hidden_chunks.get((cx, cy))

        if old:
            for n in old:
                n.detachNode()
        if oldh:
            hide = oldh.isHidden()
            oldh.detachNode()

        nps = []
        for s, b in builders.items():
            np = self.attachNewNode(b.build())
            np.setPos(cx * chunk_size, cy * chunk_size, 0)
            np.setTexture(self.substances[s])
            nps.append(np)

        hnp = self.attachNewNode(hbuilder.build())
        hnp.setPos(cx * chunk_size, cy * chunk_size, 0)
        hnp.setTexture(self.hiddentexture)
        if hide:
            hnp.hide()
        else:
            hnp.show()

        self.chunks[(cx, cy)] = nps
        self.hidden_chunks[(cx, cy)] = hnp

    def update(self, x, y):
        cx, cy = x // self.chunk_size, y // self.chunk_size
        self.updates.add((cx, cy))

        if self.show_stale_chunks:
            old = self.chunks.get((cx, cy))
            oldh = self.hidden_chunks.get((cx, cy))

            if old:
                for n in old:
                    n.setColorScale(1.0, 0.5, 0.5, 1.0)
            if oldh:
                oldh.setColorScale(1.0, 0.5, 0.5, 1.0)

    def hide_hidden(self):
        for c in self.hidden_chunks.values():
            c.hide()

    def show_hidden(self):
        for c in self.hidden_chunks.values():
            c.show()

    def perform_updates(self, task):
        if self.isHidden():
            return task.cont

        if self.updates:
            cx, cy = self.updates.pop()
            self.build_chunk(cx, cy)

        return task.again


class WorldGeometry(DirectObject):
    def __init__(self, world):
        notify.info('Initializing world geometry')

        self.world = world
        self.node = core.NodePath('world')
        # shader = core.Shader.load(core.Shader.SLGLSL, 'media/shaders/vertex.glsl', 'media/shaders/fragment.glsl')
        # self.node.setShader(shader)
        self.node.setShaderInput('color_scale', 1.0)

        self.bordertexture = loader.loadTexture('media/textures/border.png',
                                                anisotropicDegree=16,
                                                minfilter=core.Texture.FTLinearMipmapLinear)
        self.node.setShaderInput('border_texture', self.bordertexture)

        self.slices = []

        for z in range(self.world.depth):
            slice = Slice(self.world, z)
            slice.reparentTo(self.node)
            self.slices.append(slice)

            for x, y in self.world.columns():
                f, s, h = self.world.get_raw(x, y, z)
                if s and not h:
                    slice.update_all()
                    break

        self.accept('slice-changed', self.slice_changed)
        self.accept('block-update', self.block_update)
        self.accept('entity-z-change', self.reparent_entity)
        self.accept('designation-add', self.designation)

        notify.info('Initializing world geometry complete')

    def destroy(self):
        for s in self.slices:
            s.destroy()
        self.ignoreAll()
        self.node.detachNode()

    def slice_changed(self, current_slice, explore):
        for i, s in enumerate(self.slices):
            d = abs(current_slice - i)
            if explore:
                if s.chunks:
                    s.show()
                    s.setShaderInput('color_scale', 1.0)
                    s.hide_hidden()
                else:
                    s.hide()
            else:
                if i > current_slice or d > 5:
                    s.hide()
                else:
                    s.first_update()
                    s.show()
                    if d:
                        v = 0.9 - d / 8.0
                        s.setShaderInput('color_scale', v)
                    else:
                        s.setShaderInput('color_scale', 1.0)

                if d == 0:
                    s.show_hidden()
                else:
                    s.hide_hidden()

    def block_update(self, pos):
        x, y, z = pos
        self.slices[z].update(x, y)

    def update_all(self):
        for s in self.slices:
            s.update_all()

    def reparent_entity(self, ent):
        ent.node.reparentTo(self.slices[ent.z])

    def designation(self, x, y, z, n):
        n.setPos(x, y, 0)
        n.reparentTo(self.slices[z])
