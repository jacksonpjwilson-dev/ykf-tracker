#!/usr/bin/env python3
"""Generate the checked-in Xcode project without third-party tooling."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parent
def generate(basic=False):
    objects = {}
    def ident(name): return hashlib.sha1(name.encode()).hexdigest()[:24].upper()
    def ref(name): return ident(name)
    def put(key, isa, **kw):
        objects[ident(key)] = dict(isa=isa, **kw)
        return ident(key)
    def file(path, typ='sourcecode.swift'):
        return put('file:'+path, 'PBXFileReference', lastKnownFileType=typ, path=path, sourceTree='SOURCE_ROOT')
    def build(name, fileid, **kw): return put('build:'+name, 'PBXBuildFile', fileRef=fileid, **kw)
    def config(name, settings): return put('config:'+name, 'XCBuildConfiguration', buildSettings=settings, name=name.rsplit(':',1)[-1])
    def configs(name, settings):
        return put('configs:'+name, 'XCConfigurationList', buildConfigurations=[config(name+':Debug', dict(settings, DEBUG_INFORMATION_FORMAT='dwarf', SWIFT_OPTIMIZATION_LEVEL='-Onone', SWIFT_ACTIVE_COMPILATION_CONDITIONS='DEBUG BASIC_APP' if basic else 'DEBUG')),config(name+':Release', dict(settings, SWIFT_COMPILATION_MODE='wholemodule'))], defaultConfigurationIsVisible=0, defaultConfigurationName='Release')

    products = []
    targets = []
    shared = ['Shared/Models.swift', 'Shared/SharedCache.swift']
    plans = [
     ('YKFArrivals', 'com.apple.product-type.application', 'iphoneos', shared + ['Shared/BackendClient.swift','Shared/ArrivalActivity.swift','Shared/ArrivalIntents.swift'] + [str(p.relative_to(ROOT)) for p in sorted((ROOT/'YKFArrivals').glob('*.swift'))]),
     ('YKFWidgets', 'com.apple.product-type.app-extension', 'iphoneos', shared + ['Shared/ArrivalActivity.swift','Widgets/YKFWidgets.swift']),
     ('YKFWatch', 'com.apple.product-type.application', 'watchos', shared + ['Watch/YKFWatchApp.swift']),
     ('YKFWatchWidgets', 'com.apple.product-type.app-extension', 'watchos', shared + ['Watch/WatchWidget.swift']),
    ]
    if basic: plans = plans[:1]
    fileids = {}
    for name, typ, sdk, paths in plans:
        product = put('product:'+name,'PBXFileReference', explicitFileType='wrapper.app-extension' if name in ('YKFWidgets','YKFWatchWidgets') else 'wrapper.application', path=name+('.appex' if name in ('YKFWidgets','YKFWatchWidgets') else '.app'), sourceTree='BUILT_PRODUCTS_DIR')
        products.append(product)
        sources=[]
        for p in paths:
            if p not in fileids: fileids[p] = file(p)
            sources.append(build(name+':'+p,fileids[p]))
        sourcephase=put('sources:'+name,'PBXSourcesBuildPhase',buildActionMask=2147483647,files=sources,runOnlyForDeploymentPostprocessing=0)
        frameworks=put('frameworks:'+name,'PBXFrameworksBuildPhase',buildActionMask=2147483647,files=[],runOnlyForDeploymentPostprocessing=0)
        resourcefiles=[]
        if name=='YKFArrivals':
            asset='YKFArrivals/Assets.xcassets'
            fileids[asset]=file(asset,'folder.assetcatalog')
            resourcefiles.append(build(name+':assets',fileids[asset]))
        resources=put('resources:'+name,'PBXResourcesBuildPhase',buildActionMask=2147483647,files=resourcefiles,runOnlyForDeploymentPostprocessing=0)
        settings = dict(PRODUCT_NAME='$(TARGET_NAME)', PRODUCT_BUNDLE_IDENTIFIER={'YKFArrivals':'dev.jacksonwilson.ykfarrivals','YKFWidgets':'dev.jacksonwilson.ykfarrivals.widgets','YKFWatch':'dev.jacksonwilson.ykfarrivals.watch','YKFWatchWidgets':'dev.jacksonwilson.ykfarrivals.watch.widgets'}[name], INFOPLIST_FILE=('Watch/WidgetInfo.plist' if name=='YKFWatchWidgets' else 'Watch/Info.plist' if name=='YKFWatch' else 'Widgets/Info.plist' if name=='YKFWidgets' else 'YKFArrivals/Info.plist'), GENERATE_INFOPLIST_FILE='NO', SDKROOT=sdk, SWIFT_VERSION='5.0', CODE_SIGN_STYLE='Automatic', TARGETED_DEVICE_FAMILY='4' if sdk=='watchos' else '1', MARKETING_VERSION='1.0', CURRENT_PROJECT_VERSION='1', SWIFT_EMIT_LOC_STRINGS='YES', ENABLE_USER_SCRIPT_SANDBOXING='YES')
        if name=='YKFArrivals': settings.update(ASSETCATALOG_COMPILER_APPICON_NAME='AppIcon')
        if basic: settings.update(SWIFT_ACTIVE_COMPILATION_CONDITIONS='BASIC_APP')
        if sdk=='watchos': settings.update(WATCHOS_DEPLOYMENT_TARGET='10.0', SKIP_INSTALL='YES', CODE_SIGN_ENTITLEMENTS='Watch/'+name+'.entitlements')
        else: settings.update(IPHONEOS_DEPLOYMENT_TARGET='17.0', CODE_SIGN_ENTITLEMENTS=('Widgets' if name in ('YKFWidgets','YKFWatchWidgets') else 'YKFArrivals')+'/'+name+'.entitlements')
        if name in ('YKFWidgets','YKFWatchWidgets'): settings.update(SKIP_INSTALL='YES', APPLICATION_EXTENSION_API_ONLY='YES')
        if basic: settings.pop('CODE_SIGN_ENTITLEMENTS', None)
        phases=[sourcephase,frameworks,resources]
        deps=[]
        embeds = [('YKFWidgets',13,''),('YKFWatch',16,'$(CONTENTS_FOLDER_PATH)/Watch')] if name=='YKFArrivals' else [('YKFWatchWidgets',13,'')] if name=='YKFWatch' else []
        if basic: embeds=[]
        if embeds:
            for child,dest,dstpath in embeds:
                proxy=put('proxy:'+child,'PBXContainerItemProxy',containerPortal=ref('project'),proxyType=1,remoteGlobalIDString=ref('target:'+child),remoteInfo=child)
                deps.append(put('dep:'+child,'PBXTargetDependency',target=ref('target:'+child),targetProxy=proxy))
                embed=build('embed:'+child,ref('product:'+child),settings={'ATTRIBUTES':['RemoveHeadersOnCopy']})
                phases.append(put('embedphase:'+child,'PBXCopyFilesBuildPhase',buildActionMask=2147483647,dstPath=dstpath,dstSubfolderSpec=dest,files=[embed],name='Embed '+child,runOnlyForDeploymentPostprocessing=0))
        targets.append(put('target:'+name,'PBXNativeTarget', buildConfigurationList=configs(name,settings),buildPhases=phases,buildRules=[],dependencies=deps,name=name,productName=name,productReference=product,productType=typ))
    productgroup=put('products','PBXGroup',children=products,name='Products',sourceTree='<group>')
    main=put('main','PBXGroup',children=list(fileids.values())+[productgroup],sourceTree='<group>')
    put('project','PBXProject',attributes={'LastUpgradeCheck':'1600','BuildIndependentTargetsInParallel':'YES'},buildConfigurationList=configs('project',{'CLANG_ENABLE_MODULES':'YES','SWIFT_VERSION':'5.0'}),compatibilityVersion='Xcode 14.0',developmentRegion='en',hasScannedForEncodings=0,knownRegions=['en','Base'],mainGroup=main,productRefGroup=productgroup,projectDirPath='',projectRoot='',targets=targets)
    def encode(v, indent=0):
        if isinstance(v,dict): return '{\n'+''.join('\t'*(indent+1)+k+' = '+encode(x,indent+1)+';\n' for k,x in v.items())+'\t'*indent+'}'
        if isinstance(v,list): return '('+', '.join(encode(x,indent) for x in v)+')'
        if isinstance(v,int): return str(v)
        if len(v)==24 and all(c in '0123456789ABCDEF' for c in v): return v
        return json.dumps(v)
    project=ROOT/('YKFBasic.xcodeproj' if basic else 'YKFArrivals.xcodeproj')
    project.mkdir(exist_ok=True)
    (project/'project.pbxproj').write_text('// !$*UTF8*$!\n'+encode({'archiveVersion':1,'classes':{},'objectVersion':56,'objects':objects,'rootObject':ref('project')})+'\n')
    schemes=project/'xcshareddata/xcschemes'; schemes.mkdir(parents=True,exist_ok=True)
    for name,_,_,_ in plans:
        product=name+('.appex' if name in ('YKFWidgets','YKFWatchWidgets') else '.app')
        (schemes/(name+'.xcscheme')).write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
    <Scheme LastUpgradeVersion="1600" version="1.3"><BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries><BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES"><BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{ref('target:'+name)}" BuildableName="{product}" BlueprintName="{name}" ReferencedContainer="container:{project.name}"/></BuildActionEntry></BuildActionEntries></BuildAction><LaunchAction buildConfiguration="Debug" useCustomWorkingDirectory="NO" launchStyle="0" debugServiceExtension="internal" allowLocationSimulation="YES"><BuildableProductRunnable runnableDebuggingMode="0"><BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{ref('target:'+name)}" BuildableName="{product}" BlueprintName="{name}" ReferencedContainer="container:{project.name}"/></BuildableProductRunnable></LaunchAction><ProfileAction buildConfiguration="Release"/><AnalyzeAction buildConfiguration="Debug"/><ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/></Scheme>''')

if __name__ == "__main__":
    generate()
    generate(basic=True)
