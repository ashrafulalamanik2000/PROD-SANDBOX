@echo off

SET prefix=C:/sdtools/envs/aecon_real/.pixi/envs/default/Library
SET exec_prefix=C:/sdtools/envs/aecon_real/.pixi/envs/default/Library/bin
SET libdir=C:/sdtools/envs/aecon_real/.pixi/envs/default/Library/lib


IF "%1" == "--libs" echo -LC:/sdtools/envs/aecon_real/.pixi/envs/default/Library/lib -lpdalcpp & goto exit
IF "%1" == "--plugin-dir" echo bin & goto exit
IF "%1" == "--prefix" echo %prefix% & goto exit
IF "%1" == "--ldflags" echo -L%libdir% & goto exit
IF "%1" == "--defines" echo  & goto exit
IF "%1" == "--includes" echo -IC:/sdtools/envs/aecon_real/.pixi/envs/default/Library/include -IC:/sdtools/envs/aecon_real/.pixi/envs/default/Library/include/libxml2 & goto exit
IF "%1" == "--cflags" echo /DWIN32 /D_WINDOWS /W3 & goto exit
IF "%1" == "--cxxflags" echo /DWIN32 /D_WINDOWS /W3 /GR /EHsc -std=c++11 & goto exit
IF "%1" == "--version" echo 2.10.1 & goto exit


echo Usage: pdal-config [OPTIONS]
echo Options:
echo    [--cflags]
echo    [--cxxflags]
echo    [--defines]
echo    [--includes]
echo    [--libs]
echo    [--plugin-dir]
echo    [--version]

:exit
