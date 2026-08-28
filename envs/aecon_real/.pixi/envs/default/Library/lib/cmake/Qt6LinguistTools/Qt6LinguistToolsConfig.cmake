# Copyright (C) 2024 The Qt Company Ltd.
# SPDX-License-Identifier: BSD-3-Clause


####### Expanded from @PACKAGE_INIT@ by configure_package_config_file() #######
####### Any changes to this file will be overwritten by the next CMake run ####
####### The input file was QtModuleToolsConfig.cmake.in                            ########

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

cmake_minimum_required(VERSION 3.16...3.21)

include(CMakeFindDependencyMacro)

# Find required dependencies, if any.
if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/Qt6LinguistToolsDependencies.cmake")
    include("${CMAKE_CURRENT_LIST_DIR}/Qt6LinguistToolsDependencies.cmake")
endif()

# If *Dependencies.cmake exists, the variable value will be defined there.
# Don't override it in that case.
if(NOT DEFINED "Qt6LinguistTools_FOUND")
    set("Qt6LinguistTools_FOUND" TRUE)
endif()

set(__qt_LinguistTools_should_include_targets_code "TRUE")

# Do the checks inside Targets.cmake even when the file is still being generated
if(__qt_LinguistTools_should_include_targets_code)
    include("${CMAKE_CURRENT_LIST_DIR}/Qt6LinguistToolsTargetsPrecheck.cmake")
endif()

if(NOT __qt_LinguistTools_skip_include_targets_file
        AND Qt6LinguistTools_FOUND
        AND __qt_LinguistTools_should_include_targets_code
    )
    include("${CMAKE_CURRENT_LIST_DIR}/Qt6LinguistToolsTargets.cmake")
    include("${CMAKE_CURRENT_LIST_DIR}/Qt6LinguistToolsAdditionalTargetInfo.cmake")
    if(NOT QT_NO_CREATE_VERSIONLESS_TARGETS)
        include("${CMAKE_CURRENT_LIST_DIR}/Qt6LinguistToolsVersionlessTargets.cmake")
    endif()
    set(__qt_LinguistTools_targets_file_included ON)
endif()

set(__qt_LinguistTools_extra_cmake_includes "Qt6LinguistToolsMacros.cmake")
foreach(extra_cmake_include IN LISTS __qt_LinguistTools_extra_cmake_includes)
    include("${CMAKE_CURRENT_LIST_DIR}/${extra_cmake_include}")
endforeach()


if(__qt_LinguistTools_targets_file_included AND Qt6LinguistTools_FOUND)
    __qt_internal_promote_target_to_global(Qt6::lconvert)
endif()

if(__qt_LinguistTools_targets_file_included AND Qt6LinguistTools_FOUND)
    __qt_internal_promote_target_to_global(Qt6::lcheck)
endif()

if(__qt_LinguistTools_targets_file_included AND Qt6LinguistTools_FOUND)
    __qt_internal_promote_target_to_global(Qt6::lrelease)
endif()

if(__qt_LinguistTools_targets_file_included AND Qt6LinguistTools_FOUND)
    __qt_internal_promote_target_to_global(Qt6::lrelease-pro)
endif()

if(__qt_LinguistTools_targets_file_included AND Qt6LinguistTools_FOUND)
    __qt_internal_promote_target_to_global(Qt6::lupdate)
endif()

if(__qt_LinguistTools_targets_file_included AND Qt6LinguistTools_FOUND)
    __qt_internal_promote_target_to_global(Qt6::lupdate-pro)
endif()

if(__qt_LinguistTools_targets_file_included AND Qt6LinguistTools_FOUND)
    __qt_internal_promote_target_to_global(Qt6::ltext2id)
endif()
set(Qt6LinguistTools_TARGETS "Qt6::lconvert;Qt6::lcheck;Qt6::lrelease;Qt6::lrelease-pro;Qt6::lupdate;Qt6::lupdate-pro;Qt6::ltext2id")
