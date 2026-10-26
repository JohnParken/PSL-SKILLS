"""Bounded stored Word run visibility; never an authorization control."""
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
def tag(name): return '{'+W+'}'+name
MAX_STYLES=4096
MAX_CHAIN=64
MAX_RUNS=100000
class Visibility:
    def __init__(self, policy, styles=None, revisions="accepted"):
        if policy not in ('include','exclude','annotate'): raise ValueError('unsupported hidden text policy')
        self.revisions=revisions; self.policy=policy; self.styles={}; self.records=[]; self.issues=[]; self.count=0
        if styles is not None:
            for node in styles.findall(tag('style')):
                identity=node.get(tag('styleId'))
                if not identity or identity in self.styles: raise ValueError('Invalid/duplicate style ID')
                if len(self.styles)>=MAX_STYLES: raise ValueError('Style count limit exceeded')
                self.styles[identity]=node
        self.default_styles={}
        for identity,node in self.styles.items():
            if node.get(tag('default')) in ('1','true','on'):
                kind=node.get(tag('type'))
                if kind in self.default_styles: raise ValueError('Duplicate default style')
                self.default_styles[kind]=identity
        self.defaults=styles.find(tag('docDefaults')+'/'+tag('rPrDefault')+'/'+tag('rPr')) if styles is not None else None
    def properties(self, props):
        result={}
        if props is not None:
            for name in ('vanish','webHidden'):
                items=props.findall(tag(name))
                if len(items)>1: raise ValueError('Duplicate run visibility property')
                if items:
                    value=items[0].get(tag('val'),'true')
                    if value not in ('true','false','1','0','on','off'): raise ValueError('Invalid run visibility value')
                    result[name]=value in ('true','1','on')
        return result
    def chain(self, identity):
        seen=set(); nodes=[]
        while identity:
            if identity in seen: raise ValueError('Style inheritance cycle')
            if len(seen)>=MAX_CHAIN: raise ValueError('Style chain limit exceeded')
            seen.add(identity)
            if identity not in self.styles: return {},False
            node=self.styles[identity];nodes.append(node)
            parent=node.find(tag('basedOn'));identity=parent.get(tag('val')) if parent is not None else None
        result={}
        # Toggle semantics in inherited styles require a full cascade. Treat true
        # style toggles as unknown rather than applying direct-property semantics.
        reliable=True
        for node in reversed(nodes):
            values=self.properties(node.find(tag('rPr')))
            if any(values.values()): reliable=False
            result.update(values)
        return result,reliable
    def apply(self, root, part, base='/'):
        def walk(node,path,paragraph_style=None, revision_excluded=False):
            excluded_tags={'accepted':('del','moveFrom'),'original':('ins','moveTo'),'all':()}[self.revisions]
            revision_excluded = revision_excluded or node.tag in tuple(tag(x) for x in excluded_tags)
            if node.tag==tag('p'):
                style=node.find(tag('pPr')+'/'+tag('pStyle'));paragraph_style=style.get(tag('val')) if style is not None else None
            if node.tag==tag('r'):
                self.count+=1
                if self.count>MAX_RUNS: raise ValueError('Visibility run limit exceeded')
                props=node.find(tag('rPr')); values=self.properties(self.defaults); reliable=True; basis=['document-defaults']
                for identity in (paragraph_style or self.default_styles.get('paragraph'), self.default_styles.get('character')):
                    if identity:
                        inherited,known=self.chain(identity);values.update(inherited);reliable &= known;basis.append('paragraph-style:'+identity)
                style=props.find(tag('rStyle')) if props is not None else None
                if style is not None:
                    identity=style.get(tag('val'));inherited,known=self.chain(identity);values.update(inherited);reliable &= known;basis.append('run-style:'+str(identity))
                direct=self.properties(props);values.update(direct)
                if direct: basis.append('direct')
                # vanish is general hidden text; webHidden is view-dependent.
                vanish_known = reliable or 'vanish' in direct
                vanish = direct.get('vanish', values.get('vanish', False))
                web_hidden = values.get('webHidden', False)
                if vanish_known and vanish:
                    state='hidden'
                elif web_hidden:
                    state='unknown'
                    basis.append('web-view-only')
                else:
                    state='visible' if reliable else 'unknown'
                excluded=self.policy=='exclude' and state=='hidden'
                chars=sum(len(x.text or '') for x in node.iter() if x.tag in (tag('t'),tag('delText')))
                if state!='visible' or self.policy=='annotate':
                    self.records.append({'source':{'part':part,'path':path},'state':state,'action':'revision_excluded' if revision_excluded else ('excluded' if excluded else 'retained'),'character_count':chars,'basis':basis})
                if state=='unknown': self.issues.append({'stream':part,'location':path,'code':'visibility_unknown','message':'Style visibility cascade unresolved; text retained'})
                if excluded:
                    for child in node.iter():
                        if child.tag in (tag('t'),tag('delText'),tag('instrText')): child.text=None
                        if child.tag in (tag('tab'),tag('br'),tag('cr')): child.tag=tag('excludedControl')
            for index,child in enumerate(node): walk(child,path+'/'+child.tag.rsplit('}',1)[-1]+'['+str(index)+']',paragraph_style,revision_excluded)
        walk(root,base)
