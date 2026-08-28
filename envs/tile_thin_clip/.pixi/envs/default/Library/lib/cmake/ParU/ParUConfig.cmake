#-------------------------------------------------------------------------------
# SuiteSparse/ParU/cmake_modules/ParUConfig.cmake
#-------------------------------------------------------------------------------

# The following copyright and license applies to just this file only, not to
# the library itself:
# ParUConfig.cmake, Copyright (c) 2023-2024, Timothy A. Davis.
# All Rights Reserved.
# SPDX-License-Identifier: BSD-3-clause

#-------------------------------------------------------------------------------

# Finds the ParU include file and compiled library.
# The following targets are defined:
#   SuiteSparse::ParU           - for the shared library (if available)
#   SuiteSparse::ParU_static    - for the static library (if available)

# For backward compatibility the following variables are set:

# PARU_INCLUDE_DIR - where to find ParU.h
# PARU_LIBRARY     - dynamic ParU library
# PARU_STATIC      - static ParU library
# PARU_LIBRARIES   - libraries when using ParU
# PARU_FOUND       - true if ParU found

# Set ``CMAKE_MODULE_PATH`` to the parent folder where this module file is
# installed.

#-------------------------------------------------------------------------------


####### Expanded from @PACKAGE_INIT@ by configure_package_config_file() #######
####### Any changes to this file will be overwritten by the next CMake run ####
####### The input file was ParUConfig.cmake.in                            ########

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

set ( PARU_DATE "Sept 5, 2024" )
set ( PARU_VERSION_MAJOR 1 )
set ( PARU_VERSION_MINOR 0 )
set ( PARU_VERSION_PATCH 0 )
set ( PARU_VERSION "1.0.0" )

# Check for dependent targets
include ( CMakeFindDependencyMacro )

set ( _dependencies_found ON )

# Look for SuiteSparse_config, CHOLMOD, and UMFPACK targets
if ( OFF )
    if ( NOT TARGET SuiteSparse::SuiteSparseConfig )
        # First check in a common build tree
        find_dependency ( SuiteSparse_config 7.10
            PATHS ${CMAKE_SOURCE_DIR}/../SuiteSparse_config/build NO_DEFAULT_PATH )
        # Then, check in the currently active CMAKE_MODULE_PATH
        if ( NOT SuiteSparse_config_FOUND )
            find_dependency ( SuiteSparse_config 7.10 )
        endif ( )
    endif ( )

    if ( NOT TARGET SuiteSparse::CHOLMOD )
        # First check in a common build tree
        find_dependency ( CHOLMOD 5.3
            PATHS ${CMAKE_SOURCE_DIR}/../CHOLMOD/build NO_DEFAULT_PATH )
        # Then, check in the currently active CMAKE_MODULE_PATH
        if ( NOT CHOLMOD_FOUND )
            find_dependency ( CHOLMOD 5.3 )
        endif ( )
    endif ( )

    if ( NOT TARGET SuiteSparse::UMFPACK )
        # First check in a common build tree
        find_dependency ( UMFPACK 6.3
            PATHS ${CMAKE_SOURCE_DIR}/../UMFPACK/build NO_DEFAULT_PATH )
        # Then, check in the currently active CMAKE_MODULE_PATH
        if ( NOT UMFPACK_FOUND )
            find_dependency ( UMFPACK 6.3 )
        endif ( )
    endif ( )

else ( )
    if ( NOT TARGET SuiteSparse::SuiteSparseConfig )
        find_dependency ( SuiteSparse_config 7.10 )
    endif ( )
    if ( NOT TARGET SuiteSparse::CHOLMOD )
        find_dependency ( CHOLMOD 5.3 )
    endif ( )
    if ( NOT TARGET SuiteSparse::UMFPACK )
        find_dependency ( UMFPACK 6.3 )
    endif ( )
endif ( )

if ( NOT SuiteSparse_config_FOUND OR NOT CHOLMOD_FOUND OR NOT UMFPACK_FOUND )
    set ( _dependencies_found OFF )
endif ( )

# Look for OpenMP
if ( OFF AND NOT OpenMP_CXX_FOUND )
    find_dependency ( OpenMP COMPONENTS CXX )
    if ( NOT OpenMP_CXX_FOUND )
        set ( _dependencies_found OFF )
    endif ( )
endif ( )

if ( NOT _dependencies_found )
    set ( ParU_FOUND OFF )
    return ( )
endif ( )

# Import target
include ( ${CMAKE_CURRENT_LIST_DIR}/ParUTargets.cmake )

if ( OFF )
    if ( TARGET SuiteSparse::ParU )
        get_property ( _paru_aliased TARGET SuiteSparse::ParU
            PROPERTY ALIASED_TARGET )
        if ( "${_paru_aliased}" STREQUAL "" )
            target_include_directories ( SuiteSparse::ParU SYSTEM AFTER INTERFACE
                "$<TARGET_PROPERTY:OpenMP::OpenMP_CXX,INTERFACE_INCLUDE_DIRECTORIES>" )
        else ( )
            target_include_directories ( ${_paru_aliased} SYSTEM AFTER INTERFACE
                "$<TARGET_PROPERTY:OpenMP::OpenMP_CXX,INTERFACE_INCLUDE_DIRECTORIES>" )
        endif ( )
    endif ( )
    if ( TARGET SuiteSparse::ParU_static )
        get_property ( _paru_aliased TARGET SuiteSparse::ParU_static
            PROPERTY ALIASED_TARGET )
        if ( "${_paru_aliased}" STREQUAL "" )
            target_include_directories ( SuiteSparse::ParU_static SYSTEM AFTER INTERFACE
                "$<TARGET_PROPERTY:OpenMP::OpenMP_CXX,INTERFACE_INCLUDE_DIRECTORIES>" )
        else ( )
            target_include_directories ( ${_paru_aliased} SYSTEM AFTER INTERFACE
                "$<TARGET_PROPERTY:OpenMP::OpenMP_CXX,INTERFACE_INCLUDE_DIRECTORIES>" )
        endif ( )
    endif ( )
endif ( )

# The following is only for backward compatibility with FindParU.

set ( _target_shared SuiteSparse::ParU )
set ( _target_static SuiteSparse::ParU_static )
set ( _var_prefix "PARU" )

if ( NOT ON AND NOT TARGET ${_target_shared} )
    # make sure there is always an import target without suffix )
    add_library ( ${_target_shared} ALIAS ${_target_static} )
endif ( )

get_target_property ( ${_var_prefix}_INCLUDE_DIR ${_target_shared} INTERFACE_INCLUDE_DIRECTORIES )
if ( ${_var_prefix}_INCLUDE_DIR )
    # First item in SuiteSparse targets contains the "main" header directory.
    list ( GET ${_var_prefix}_INCLUDE_DIR 0 ${_var_prefix}_INCLUDE_DIR )
endif ( )
get_target_property ( ${_var_prefix}_LIBRARY ${_target_shared} IMPORTED_IMPLIB )
if ( NOT ${_var_prefix}_LIBRARY )
    get_target_property ( _library_chk ${_target_shared} IMPORTED_LOCATION )
    if ( EXISTS ${_library_chk} )
        set ( ${_var_prefix}_LIBRARY ${_library_chk} )
    endif ( )
endif ( )
if ( TARGET ${_target_static} )
    get_target_property ( ${_var_prefix}_STATIC ${_target_static} IMPORTED_LOCATION )
endif ( )

# Check for most common build types
set ( _config_types "Debug" "Release" "RelWithDebInfo" "MinSizeRel" "None" )

get_property ( _isMultiConfig GLOBAL PROPERTY GENERATOR_IS_MULTI_CONFIG )
if ( _isMultiConfig )
    # For multi-configuration generators (e.g., Visual Studio), prefer those
    # configurations.
    list ( PREPEND _config_types ${CMAKE_CONFIGURATION_TYPES} )
else ( )
    # For single-configuration generators, prefer the current configuration.
    list ( PREPEND _config_types ${CMAKE_BUILD_TYPE} )
endif ( )

list ( REMOVE_DUPLICATES _config_types )

foreach ( _config ${_config_types} )
    string ( TOUPPER ${_config} _uc_config )
    if ( NOT ${_var_prefix}_LIBRARY )
        get_target_property ( _library_chk ${_target_shared}
            IMPORTED_IMPLIB_${_uc_config} )
        if ( EXISTS ${_library_chk} )
            set ( ${_var_prefix}_LIBRARY ${_library_chk} )
        endif ( )
    endif ( )
    if ( NOT ${_var_prefix}_LIBRARY )
        get_target_property ( _library_chk ${_target_shared}
            IMPORTED_LOCATION_${_uc_config} )
        if ( EXISTS ${_library_chk} )
            set ( ${_var_prefix}_LIBRARY ${_library_chk} )
        endif ( )
    endif ( )
    if ( TARGET ${_target_static} AND NOT ${_var_prefix}_STATIC )
        get_target_property ( _library_chk ${_target_static}
            IMPORTED_LOCATION_${_uc_config} )
        if ( EXISTS ${_library_chk} )
            set ( ${_var_prefix}_STATIC ${_library_chk} )
        endif ( )
    endif ( )
endforeach ( )

set ( PARU_LIBRARIES ${PARU_LIBRARY} )

macro ( suitesparse_check_exist _var _files )
  # ignore generator expressions
  string ( GENEX_STRIP "${_files}" _files2 )

  foreach ( _file ${_files2} )
    if ( NOT EXISTS "${_file}" )
      message ( FATAL_ERROR "File or directory ${_file} referenced by variable ${_var} does not exist!" )
    endif ( )
  endforeach ()
endmacro ( )

suitesparse_check_exist ( PARU_INCLUDE_DIR ${PARU_INCLUDE_DIR} )
suitesparse_check_exist ( PARU_LIBRARY ${PARU_LIBRARY} )

message ( STATUS "ParU version: ${PARU_VERSION}" )
message ( STATUS "ParU include: ${PARU_INCLUDE_DIR}" )
message ( STATUS "ParU library: ${PARU_LIBRARY}" )
message ( STATUS "ParU static:  ${PARU_STATIC}" )
