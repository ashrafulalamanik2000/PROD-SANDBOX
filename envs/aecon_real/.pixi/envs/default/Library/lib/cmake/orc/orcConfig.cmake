# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
# This config sets the following variables in your project:
#
#   orc_VERSION - version of the found ORC
#   orc_FOUND - true if ORC found on the system
#
# This config sets the following targets in your project:
#
#   orc::orc - for linked as static library
#
# For backward compatibility, this config also sets the following variables:
#
#   ORC_FOUND - same as orc_FOUND above
#   ORC_STATIC_LIB - static library of the found ORC
#   ORC_INCLUDE_DIR - include directory of the found ORC
#   ORC_INCLUDE_DIRS - same as ORC_INCLUDE_DIR above


####### Expanded from @PACKAGE_INIT@ by configure_package_config_file() #######
####### Any changes to this file will be overwritten by the next CMake run ####
####### The input file was orcConfig.cmake.in                            ########

get_filename_component(PACKAGE_PREFIX_DIR "${CMAKE_CURRENT_LIST_DIR}/../../../" ABSOLUTE)

macro(set_and_check _var _file)
  set(${_var} "${_file}")
  if(NOT EXISTS "${_file}")
    message(FATAL_ERROR "File or directory ${_file} referenced by variable ${_var} does not exist !")
  endif()
endmacro()

macro(check_required_components _NAME)
  foreach(comp ${${_NAME}_FIND_COMPONENTS})
    if(NOT ${_NAME}_${comp}_FOUND)
      if(${_NAME}_FIND_REQUIRED_${comp})
        set(${_NAME}_FOUND FALSE)
      endif()
    endif()
  endforeach()
endmacro()

####################################################################################

set(ORC_SYSTEM_DEPENDENCIES "ProtobufAlt;SnappyAlt;ZLIBAlt;ZSTDAlt;LZ4Alt")

if(DEFINED CMAKE_MODULE_PATH)
  set(ORC_CMAKE_MODULE_PATH_OLD ${CMAKE_MODULE_PATH})
else()
  unset(ORC_CMAKE_MODULE_PATH_OLD)
endif()
set(CMAKE_MODULE_PATH "${CMAKE_CURRENT_LIST_DIR}")

include(CMakeFindDependencyMacro)
foreach(dependency ${ORC_SYSTEM_DEPENDENCIES})
  find_dependency(${dependency})
endforeach()

if(DEFINED ORC_CMAKE_MODULE_PATH_OLD)
  set(CMAKE_MODULE_PATH ${ORC_CMAKE_MODULE_PATH_OLD})
  unset(ORC_CMAKE_MODULE_PATH_OLD)
else()
  unset(CMAKE_MODULE_PATH)
endif()

include("${CMAKE_CURRENT_LIST_DIR}/orcTargets.cmake")

get_target_property(orc_static_configurations orc::orc IMPORTED_CONFIGURATIONS)

check_required_components(orc)

foreach(BUILD_TYPE_SUFFIX
        "_RELEASE"
        "_RELWITHDEBINFO"
        "_MINSIZEREL"
        "_DEBUG"
        "")
  if(NOT ORC_STATIC_LIB)
    get_target_property(ORC_STATIC_LIB orc::orc IMPORTED_LOCATION${BUILD_TYPE_SUFFIX})
  endif()
endforeach()

get_target_property(ORC_INCLUDE_DIR orc::orc INTERFACE_INCLUDE_DIRECTORIES)

set(ORC_FOUND TRUE)
set(ORC_VERSION ${orc_VERSION})
set(ORC_INCLUDE_DIRS ${ORC_INCLUDE_DIR})
