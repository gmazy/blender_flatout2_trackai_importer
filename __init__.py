# ##### BEGIN LICENSE BLOCK #####
#
# This program is licensed under Creative Commons CC0
# https://creativecommons.org/publicdomain/zero/1.0/
#
# ##### END LICENSE BLOCK #####

bl_info = {  
    "name": "Import Flatout 2 & UC trackai.bin",  
    "author": "Mazay",  
    "version": (0, 2),  
    "blender": (2, 80, 0),  
    "location": "File > Import",  
    "description": "Import trackai.bin. Still work in progress, exports are not possible.",  
    "warning": "",  
    "wiki_url": "",  
    "tracker_url": "",  
    "category": "Import-Export"}  


import bpy
import os
import binascii
import struct
import subprocess, sys
import re
import random
import bmesh
import mathutils
import hashlib
import time
import math
import tempfile
import addon_utils

# ExportHelper is a helper class, defines filename and
# invoke() function which calls the file selector.
from bpy_extras.io_utils import ImportHelper, ExportHelper
from bpy.props import StringProperty, BoolProperty, IntProperty, EnumProperty
from bpy.types import Operator, AddonPreferences

class io_import_fotrackai(AddonPreferences):
    # this must match the addon name, use '__package__'
    # when defining this in a submodule of a python package.
    bl_idname = __name__

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "breckfestPath")
        

class ImportFoTrackai(bpy.types.Operator, ImportHelper):
    '''Imports Wreckfest SCNE or VHCL file'''
    bl_idname = "import_fotrackai.bin"  # this is important since its how bpy.ops.import.some_data is constructed
    bl_label = "Import SCNE"
    bl_options = {"UNDO"}

    filter_glob : StringProperty(default="*.bin", options={'HIDDEN'})  
    
    # Selected files
    files : bpy.props.CollectionProperty(type=bpy.types.PropertyGroup)

    # List of operator properties    
    debug : BoolProperty(name="Debug mode", description="Import track sector numbers and metadata", default=True)

    def execute(self, context):
        keywords = self.as_keywords(ignore=('filter_glob','filepath','files')) # Include operator properties
        start = time.time()
        folder = (os.path.dirname(self.filepath))
        for file in self.files:
            path_and_file = (os.path.join(folder, file.name))
            read_trackai(context, path_and_file, **keywords)
        bpy.context.window.cursor_set('NONE') # Temporary fix to clear loading cursor, B 2.90
        bpy.context.window.cursor_set('DEFAULT')
        print('\nFinished in', round(time.time()-start,2), 's')
        return {'FINISHED'}


#Add to File>Import Menu
def menu_func_import(self, context):
    self.layout.operator(ImportFoTrackai.bl_idname, text="Flatout 2 trackai.bin")

#Only function blender calls on load
def register(): 
    bpy.utils.register_class(ImportFoTrackai)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.utils.register_class(io_import_fotrackai)

def unregister():
    bpy.utils.unregister_class(ImportFoTrackai)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.utils.unregister_class(io_import_fotrackai)


if __name__ == "__main__":
    register()



#--------------------------------------------------------------------------------
# import

class TrackAiParse:
    def __init__(self,bytes = 0):
        self.bytes = bytes #scne data
        self.p = 0 #pointer
        self.endian = '<' #Endianess < = litte

    def readBytes(self,length=4): #read raw bytes by length, move pointer
        data = self.bytes[self.p:self.p+length]
        self.p += length
        return data    
    
    def skip(self,length=4): #move pointer
        self.p += length

    def tell(self): #get pointer position
        return self.p
  
    def read(self, param): #struct.unpack
        data = self.readBytes(struct.calcsize(param))
        return struct.unpack(self.endian+param, data)

    def i(self,length=4): #int
        data = self.readBytes(length)
        bo = 'little' if self.endian=='<' else 'big'
        return int.from_bytes(data, byteorder=bo, signed=True)
        
    def f(self,length=4): #float
        data = self.readBytes(length)
        return struct.unpack(self.endian+'f', data)[0]

    def xyz(self,length=12): #float,float,float
        x,z,y = self.read('fff')
        return [x,y,z]

    def checkHeader(self,headerCheck): # Check data against hex string
        check = bytes.fromhex(headerCheck.replace(' ', ''))
        header = self.readBytes(len(check))
        if(header != check): # Compare as int
            popup( header.hex() +' -header found, expected '+str(headerCheck) )
        return header.hex()
    
    def matrix(self, length=36): #transform matrix 3x3 float, 36 bytes
        data = self.readBytes(length)
        v = struct.unpack(self.endian+'9f', data)
        #matrix
        x = (v[0], v[1], v[2])
        y = (v[3], v[4], v[5])
        z = (v[6], v[7], v[8])
        #swapping Y and Z (third and second row, and third and second column    
        mx = (x[0],x[2],x[1]), (z[0],z[2],z[1]), (y[0],y[2],y[1])    
        return mx
        
 


def show_messagebox(message = "", title = "Message Box", icon = 'INFO'):
    def draw(self, context):
        self.layout.label(text=message)
    bpy.context.window_manager.popup_menu(draw, title = title, icon = icon)

#Error popup
def popup(title):
    show_messagebox("Error", title, 'ERROR')
    print(title) #error to console

def check_meshdata(verts, faces, name=''):
    maxvert = len(verts)  
    for face in faces:
        for x in face:
            if(x<0 or x>(maxvert-1)): 
                popup("\nIncorrect triangle data at "+name+", reference to vert: "+str(x)+"\n")
                return False
    return True

def origin_to_geometry(ob):
    center = mathutils.Vector((0, 0, 0))
    numVert = 0
    oldLoc = ob.location
    for v in ob.data.vertices: # Sum all
        center += v.co #x,y,z
        numVert += 1
    center = center/numVert # Divide to get average point
    movement = oldLoc - center
    for v in ob.data.vertices:# Move all vertices
        v.co += movement
    ob.location -= movement# Move object to opposite direction

def create_mesh_ob(name, verts, faces, meshname='', collection='', matrix='', reset_origin=True, draw_type='TEXTURED', show_wire=False, color='', colorname='', subCollection=False): 
    if (check_meshdata(verts,faces,meshname) == False): return False

    mesh = bpy.data.meshes.new(meshname)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    
    ob = bpy.data.objects.new(name, mesh)
    ob.display_type = draw_type
    ob.show_wire = show_wire
    ob.show_all_edges = True
    if matrix != '':
        ob.matrix_world = matrix

    if (color != ''):
        if (colorname != ''):
            if (colorname in bpy.data.materials):  mat = bpy.data.materials.get(colorname)
            else: mat = bpy.data.materials.new(colorname)
        else:
            mat = bpy.data.materials.new("route")           
        mat.diffuse_color = color
        ob.active_material = mat

    link_to_collection(ob, collection)
    if(reset_origin): origin_to_geometry(ob)

    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    return ob

def create_empty_ob(name, type='CUBE', size=1.0, collection=''):
    ob = bpy.data.objects.new(name, object_data=None)
    link_to_collection(ob, collection)
    ob.empty_display_size = size
    ob.empty_display_type = type
    bpy.context.view_layer.update()
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    return ob

def new_collection(collectionName, parentCollection=''):
    col = bpy.data.collections.new(collectionName)
    parent = bpy.context.scene.collection.children.get(parentCollection)
    if parentCollection == '': # Add to requested collection.
        bpy.context.scene.collection.children.link(col)
    elif parent: # Add to sub-collection instead.
        parent.children.link(col)
        
def link_to_collection(obj, collectionName):
    if(collectionName==''):
        bpy.context.scene.collection.objects.link( obj )
    else:
        if collectionName not in bpy.data.collections: new_collection(collectionName) #make new collection
        layer = bpy.context.view_layer.layer_collection # Current view layer Collection
        if(collectionName in layer.children and layer.children[collectionName].exclude == False): # Prevent error by excluded collection
            bpy.data.collections[collectionName].objects.link( obj ) #add to collection, 
        else:
            bpy.context.scene.collection.objects.link( obj ) #add without collection, if collection excluded or moved to subcollection.


#### AIROUTE #### 
def make_airoute(get, route, debug):
    get.checkHeader('76 02 29 00')
    num_sec = get.i()   # 75, 12
    print('',num_sec,'sectors')

    csv = ''
    faces = []
    vertsRoute = []
    vertsSafe = []
    vertsMiddle = []
    count = 0

    for s in range(num_sec): # 75 sectors in forest1> a> data> trackai.bin
        count += 2

        # Make face between previous and current sector 
        if(s != 0): #skipping first sector
            faces += (count-1, count-2, count-4, count-3), 

        get.checkHeader('76 02 23 00') 

        # Skipping unknown values
        get.i() # B Sector number: 1, 2, 3 ... 74
        get.i() # C 0
        get.i() # D Previous sector number?: 75, 0, 1....
        get.f() # E 0
        get.xyz() # X,Z,Y Normal1
        get.xyz() # X,Z,Y Normal2
        get.xyz() # X,Z,Y Normal3

        L = get.xyz() # Route left vert X,Z,Y
        R = get.xyz() # Route right vert X,Z,Y
        vertsRoute += L, R,

        LSafe = get.xyz() # Safe route left vert X,Z,Y
        RSafe = get.xyz() # Safe route right vert X,Z,Y
        vertsSafe += LSafe, RSafe,

        vertsMiddle += [get.xyz()] # Race line X,Z,Y

        get.xyz() # X,Z,Y Normal4
        get.xyz() # X,Z,Y Normal5
        get.xyz() # X,Z,Y Normal6
        get.f() # AM Big updown changes here
        get.f() # AN Big updown changes here
        get.f() # AO Total elapsed meters?  0 > 16 > 36 ... 1455
        get.f() # AP -1082130432
        get.f() # AQ
        get.f() # AR 0 / 1
        get.f() # AS 1
        get.f() # AT
        get.f() # AU 0 / 1
        get.f() # AV Sector number: 0, 1 ... 75
        get.f() # AW 0
        get.f() # AX 0 / 1
        get.f() # AY 0
        get.checkHeader('76 02 24 00')


    if(route==0): ending = "main"
    else: ending = "alt"+str(route)

    ai_safe_ob = create_mesh_ob("#ai_safe_"+ending, vertsRoute, faces, meshname="route", show_wire=True, color=(1, 0, 0, 0.15), colorname="red-route", collection="Airoutes")
    ai_safe_ob.color = (1,0,0, 1)
    ai_race_ob = create_mesh_ob("#ai_race_"+ending, vertsSafe, faces, meshname="route", show_wire=True, color=(0, 0.8, 0.1, 1), colorname="green-route", collection="Airoutes")
    ai_race_ob.color = (0,1,0, 1)


    ai_middle_ob = create_mesh_ob("#ai_raceline_"+ending, vertsMiddle, '', meshname="route", show_wire=True, color=(1, 1, 1, 1), colorname="midline", collection="Airoutes")
    ai_middle_ob.color = (1,1,1, 1)

    #make_airoute_subsection(get,route) # 75x
    uk1 = get.i()   # 0, 1, 1 `# Start Index Of Mainroute Sector ?
    uk2 = get.i()   # 1056964608 # End Index Of mainroute sector ?
    uk3 = get.i()   # 0
    uk4 = get.i()   # 0
    uk5 = get.i()   # 2
    get.checkHeader('76 02 26 00')


#### STARTPOINTS ####
def make_startpoints(get):
    get.checkHeader('76 08 30 00')
    num = get.i()  # 8
    print('\nFound',num,'Startpoints')
    for x in range(num):
        position = get.xyz() 
        mx = get.matrix() #3x3 
        ob = create_empty_ob('#startpoint', 'CUBE', size=0.5, collection='Startpoints') #1m x 1m x 1m size
        ob.matrix_world = mathutils.Matrix(mx).to_4x4()
        ob.location = position
        ob.scale = (2, 5, 1) #car placeholder 2m x 5m x 1m
    get.checkHeader('76 08 31 00')


#### CHECKPOINTS ####      
def make_checkpoints(get):
    get.checkHeader('76 09 01 00') # Start
    num = get.i() # 8
    print('Found',num,'Checkpoints')
    for cp in range(num):
        middleX, middleZ, middleY = get.f(), get.f(), get.f()
        LX, LZ, LY = get.f(), get.f(), get.f()
        RX, RZ, RY = get.f(), get.f(), get.f()

        # Simple plane
        faces = ((0,1,2,3),)
        verts = ((LX,LY,LZ+25), (RX,RY,RZ+25), (RX,RY,RZ-25), (LX,LY,LZ-25))
        create_mesh_ob("#checkpoint",verts,faces,"checkpoint", collection="Checkpoints")
    get.checkHeader('76 09 02 00') # End

#### AIROUTES Main #### 
def make_airoute_main(get,debug):
    vertsRoute = []
    faces = []
    get.checkHeader('76 02 29 00')
    num = get.i() #124
    print('Found',num,'Airoute Main Combined Sectors')
    get.checkHeader('76 03 02 00') # Start
    uk = get.i() #0
    count = 0

    for s in range(num):
        count += 2
        # Make face between previous and current sector 
        if(s != 0): #skipping first sector
            faces += (count-1, count-2, count-4, count-3), 


        uk1 = get.i() # 39, 40, 41
        L = get.xyz() # Route left vert X,Z,Y
        R = get.xyz() # Route right vert X,Z,Y
        uk2 = get.i() # 39, 40, 41
        
        vertsRoute += L, R,

        if(debug):
            ob = create_empty_ob(str(s)+'  L ('+str(uk1)+' '+str(uk2)+')', type='SINGLE_ARROW', collection='Airoute Main Sectors (debug mode)')
            ob.location = L
            ob.show_name = True
            ob.show_in_front = True
            ob = create_empty_ob(str(s)+'  R', type='SINGLE_ARROW', collection='Airoute Main Sectors (debug mode)')
            ob.location = R
            ob.show_name = True
            ob.show_in_front = True

    ai_route_ob = create_mesh_ob("#ai_route_main", vertsRoute, faces, meshname="route", show_wire=True, color=(0, 0, 1, 0), colorname="blue-route", collection="Airoutes Main (wip, broken)")
    ai_route_ob.color = (0,0,1, 1) #Object color

    get.p = get.p-4 # Seek -4, Something is broken here, fix for now.
    get.checkHeader('76 03 03 00') # End

    ### Unknown ###
    num = get.i() # 145
    uk = get.i()
    for i in range(num):
        A = get.i()
        B = get.i()
        #print(A,B)
    get.checkHeader('76 03 05 00')
    get.checkHeader('76 03 01 00')


def read_trackai_file(get, debug):
    '''Read sections from file in order'''

    get.checkHeader('76 02 27 00') # Start

    num_routes = get.i()   # 4
    print('\nFound',num_routes,' Airoutes')
    for route in range(num_routes):
        make_airoute(get,route,debug)

    get.checkHeader('76 08 28 00')  # Start
    make_startpoints(get)
    make_checkpoints(get)

    get.checkHeader('76 08 29 00') # End

    make_airoute_main(get,debug)

    get.checkHeader('76 02 28 00') # End



def read_trackai(context, filepath, debug=False):  
    print ("\n\nImporting from:",filepath)
    if(filepath.split('//')[-1] == ''): return {'FINISHED'} # not file selected

    with open(filepath, 'rb') as f:
        get = TrackAiParse(f.read()) 

    # Quit if data not found
    if(not get.bytes):
        return {'FINISHED'}

    # Read file
    read_trackai_file(get,debug)

    # Increase screen clipping distance
    for a in bpy.context.screen.areas:
        if a.type == 'VIEW_3D':
            break
    if(a.spaces.active.clip_end == 1000):
        a.spaces.active.clip_end = 8000

    return {'FINISHED'}
