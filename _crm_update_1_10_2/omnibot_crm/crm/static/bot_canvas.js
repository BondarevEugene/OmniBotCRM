(() => {
  const root = document.getElementById('bot-canvas');
  if (!root) return;
  const $ = id => document.getElementById(id);
  const url = root.dataset.url, csrf = root.dataset.csrf;
  const specs = {
    start:['▶','Старт','Триггер сценария'], message:['✉','Сообщение','Текст пользователю'],
    question:['?','Вопрос','Записать ответ'], buttons:['▦','Кнопки','Выбор варианта'],
    condition:['◇','Условие','Ветка да / нет'], media:['▧','Медиа','HTTPS файл'],
    wait:['◷','Пауза','Задержка'], handoff:['♙','Оператор','Передача менеджеру'],
    end:['■','Конец','Завершить диалог']
  };
  let data, selected = null, armed = null, dirty = false, answers = {};
  const escape = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
  async function call(path='', options={}) {
    const response = await fetch(url + path, {credentials:'same-origin', ...options,
      headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...options.headers}});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(Array.isArray(body.detail) ? body.detail.join(' · ') : body.detail || `HTTP ${response.status}`);
    return body;
  }
  function status(message) { $('bc-status').textContent = message; }
  function changed() { dirty=true;status('● Несохранённые изменения'); }
  function handles(node) {
    if (node.type==='end'||node.type==='handoff') return [];
    if (node.type==='condition') return [['yes','Да'],['no','Нет']];
    if (node.type==='buttons') return (node.buttons||[]).map((text,i)=>['button_'+i,text]);
    return [['next','Далее']];
  }
  function render() {
    if (!data) return;
    $('bc-count').textContent=`${data.graph.nodes.length} блоков · ${data.graph.edges.length} связей`;
    $('bc-nodes').innerHTML=data.graph.nodes.map(node=>{
      const spec=specs[node.type]||specs.message;
      const short=node.type==='buttons'?(node.buttons||[]).join(' / '):node.type==='condition'?`${node.variable} = ${node.equals}`:node.type==='media'?node.media_url:node.type==='wait'?`${node.seconds} сек.`:node.type==='handoff'?node.queue:node.text;
      return `<article class="bc-node ${node.id===selected?'active':''}" data-id="${escape(node.id)}" style="left:${node.x}px;top:${node.y}px"><button class="bc-port bc-input-port" data-target="${escape(node.id)}" title="Вход"></button><header><span class="bc-icon">${spec[0]}</span><b>${escape(node.label||spec[1])}</b></header><div class="bc-summary">${escape(short||spec[2]).slice(0,130)}</div><div class="bc-ports">${handles(node).map(([key,label])=>`<div class="bc-portline"><span>${escape(label||key)}</span><button class="bc-port ${armed?.id===node.id&&armed?.handle===key?'armed':''}" data-source="${escape(node.id)}" data-handle="${escape(key)}" title="Соединить"></button></div>`).join('')}</div></article>`;
    }).join('');
    $('bc-nodes').querySelectorAll('.bc-node').forEach(card=>{
      card.onclick=e=>{if(e.target.closest('.bc-port'))return;selected=card.dataset.id;render();};
      card.querySelector('header').onpointerdown=e=>drag(e,card);
    });
    $('bc-nodes').querySelectorAll('[data-source]').forEach(port=>port.onclick=e=>{
      e.stopPropagation();armed={id:port.dataset.source,handle:port.dataset.handle};status('Выберите вход следующего блока');render();
    });
    $('bc-nodes').querySelectorAll('[data-target]').forEach(port=>port.onclick=e=>{
      e.stopPropagation();if(!armed)return status('Сначала выберите выход блока');
      if(armed.id===port.dataset.target)return status('Нельзя соединить блок с собой');
      data.graph.edges=data.graph.edges.filter(link=>!(link.source===armed.id&&link.handle===armed.handle));
      data.graph.edges.push({id:'e_'+crypto.randomUUID().replace(/-/g,'').slice(0,16),source:armed.id,target:port.dataset.target,handle:armed.handle});
      armed=null;changed();render();
    });
    inspect();requestAnimationFrame(wires);
  }
  function drag(event,card) {
    if(event.button!==0)return;event.preventDefault();
    const node=data.graph.nodes.find(n=>n.id===card.dataset.id),x=node.x,y=node.y,px=event.clientX,py=event.clientY;
    const move=e=>{node.x=Math.max(0,Math.min(1600,Math.round(x+e.clientX-px)));node.y=Math.max(0,Math.min(1200,Math.round(y+e.clientY-py)));card.style.left=node.x+'px';card.style.top=node.y+'px';wires();};
    const stop=()=>{window.removeEventListener('pointermove',move);if(x!==node.x||y!==node.y)changed();};
    window.addEventListener('pointermove',move);window.addEventListener('pointerup',stop,{once:true});
  }
  function wires() {
    if(!data)return;
    const svg=$('bc-wires'),plane=$('bc-plane').getBoundingClientRect();svg.innerHTML='';
    data.graph.edges.forEach(edge=>{
      const a=[...document.querySelectorAll('#bc-nodes [data-source]')].find(el=>el.dataset.source===edge.source&&el.dataset.handle===edge.handle);
      const b=[...document.querySelectorAll('#bc-nodes [data-target]')].find(el=>el.dataset.target===edge.target);
      if(!a||!b)return;
      const ar=a.getBoundingClientRect(),br=b.getBoundingClientRect(),x=ar.left+ar.width/2-plane.left,y=ar.top+ar.height/2-plane.top,tx=br.left+br.width/2-plane.left,ty=br.top+br.height/2-plane.top;
      const bend=Math.max(55,Math.abs(tx-x)*.45),path=document.createElementNS('http://www.w3.org/2000/svg','path');
      path.setAttribute('d',`M ${x} ${y} C ${x+bend} ${y}, ${tx-bend} ${ty}, ${tx} ${ty}`);path.setAttribute('class','bc-wire');
      path.onclick=()=>{if(confirm('Удалить связь?')){data.graph.edges=data.graph.edges.filter(e=>e.id!==edge.id);changed();render();}};svg.append(path);
    });
  }
  function inspect() {
    const element=$('bc-inspector'),node=data.graph.nodes.find(n=>n.id===selected);
    if(!node){element.innerHTML='<p class="bc-empty">Нажмите на карточку, чтобы изменить её параметры. Выход блока подключается к входу другого блока.</p>';return;}
    const field=(id,label,value,multi=false)=>`<label class="bc-field">${label}${multi?`<textarea id="bc-${id}">${escape(value)}</textarea>`:`<input id="bc-${id}" value="${escape(value)}">`}</label>`;
    element.innerHTML=`<b>${escape(specs[node.type][1])}</b>${field('label','Название',node.label)}${['message','question'].includes(node.type)?field('text','Текст',node.text,true):''}${['question','condition'].includes(node.type)?field('variable','Имя переменной',node.variable):''}${node.type==='condition'?field('equals','Равно',node.equals):''}${node.type==='buttons'?field('buttons','Кнопки: каждая с новой строки',(node.buttons||[]).join('\n'),true):''}${node.type==='media'?field('media_url','HTTPS ссылка',node.media_url):''}${node.type==='wait'?field('seconds','Секунд',node.seconds):''}${node.type==='handoff'?field('queue','Команда операторов',node.queue):''}<button class="bc-btn" id="bc-delete">Удалить блок</button>`;
    for(const prop of ['label','text','variable','equals','media_url','queue','seconds']){
      const input=$('bc-'+prop);if(!input)continue;
      input.oninput=()=>{node[prop]=prop==='seconds'?Number(input.value||0):input.value;changed();};
      input.onchange=()=>{render();};
    }
    if(node.type==='buttons')$('bc-buttons').onchange=e=>{
      node.buttons=e.target.value.split('\n').map(v=>v.trim()).filter(Boolean).slice(0,8);
      data.graph.edges=data.graph.edges.filter(link=>link.source!==node.id||Number(link.handle.replace('button_',''))<node.buttons.length);
      changed();render();
    };
    $('bc-delete').onclick=()=>{if(!confirm('Удалить блок и его связи?'))return;
      data.graph.nodes=data.graph.nodes.filter(n=>n.id!==node.id);
      data.graph.edges=data.graph.edges.filter(e=>e.source!==node.id&&e.target!==node.id);
      selected=null;changed();render();};
  }
  function add(type) {
    const id='n_'+crypto.randomUUID().replace(/-/g,'').slice(0,14),count=data.graph.nodes.length;
    data.graph.nodes.push({id,type,label:specs[type][1],text:type==='message'?'Ваше сообщение':type==='question'?'Как вас зовут?':'',variable:type==='question'?'client_name':'',equals:'',buttons:type==='buttons'?['Да','Нет']:[],media_url:'',queue:'',seconds:5,x:140+(count%4)*290,y:120+Math.floor(count/4)*180});
    selected=id;changed();render();
  }
  async function save() {
    const result=await call('',{method:'PUT',body:JSON.stringify({version:data.version,graph:data.graph})});
    data.version=result.version;dirty=false;status('✓ Сохранено · версия '+data.version);
  }
  async function preview() {
    $('bc-chat').hidden=false;
    const result=await call('/preview',{method:'POST',body:JSON.stringify({graph:data.graph,inputs:answers})});
    $('bc-chat').innerHTML=(result.messages||[]).map(m=>`<div class="bc-bubble ${m.from==='user'?'user':''}">${escape(m.text)}</div>`).join('')||'<div class="bc-bubble">Запуск сценария</div>';
    const box=$('bc-answer');box.innerHTML='';
    if(result.errors)return status(result.errors.join(' · '));
    if(!result.awaiting)return status(result.finished?'Тест завершён':'Тест остановлен');
    const waiting=result.awaiting;
    if(waiting.kind==='text'){
      box.innerHTML='<label class="bc-field">Ответ клиента<input id="bc-reply"></label><button class="bc-btn" id="bc-send">Отправить</button>';
      $('bc-send').onclick=()=>{answers[waiting.node]=$('bc-reply').value;preview().catch(err=>status(err.message));};
      $('bc-reply').onkeydown=e=>{if(e.key==='Enter')$('bc-send').click();};
    }else waiting.options.forEach(option=>{const button=document.createElement('button');button.className='bc-btn';button.textContent=option;button.onclick=()=>{answers[waiting.node]=option;preview().catch(err=>status(err.message));};box.append(button);});
  }
  $('bc-palette').innerHTML=Object.entries(specs).map(([key,[icon,title,hint]])=>`<button data-kind="${key}">${icon} ${title}<small>${hint}</small></button>`).join('');
  $('bc-palette').querySelectorAll('[data-kind]').forEach(button=>button.onclick=()=>add(button.dataset.kind));
  $('bc-save').onclick=()=>save().catch(err=>status(err.message));
  $('bc-check').onclick=async()=>{try{const result=await call('/check',{method:'POST',body:JSON.stringify(data.graph)});status(result.valid?'✓ Схема готова к передаче':result.errors.join(' · '));}catch(err){status(err.message);}};
  $('bc-preview').onclick=()=>{answers={};preview().catch(err=>status(err.message));};
  $('bc-export').onclick=async()=>{try{if(dirty)await save();const manifest=await call('/export');const link=document.createElement('a'),blob=new Blob([JSON.stringify(manifest,null,2)],{type:'application/json'});link.href=URL.createObjectURL(blob);link.download=`${manifest.bot_key}-${manifest.scenario_key}-v${manifest.version}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);}catch(err){status(err.message);}};
  window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
  window.addEventListener('resize',wires);
  call().then(result=>{data=result;status('Схема загружена · версия '+data.version);render();}).catch(err=>status(err.message));
})();
