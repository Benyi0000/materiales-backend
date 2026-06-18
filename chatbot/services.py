import json
from django.conf import settings

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.utilities import DuckDuckGoSearchAPIWrapper

from .models import ChatbotSession, ChatbotMessage
from catalog.utils import get_google_api_key

class RAGQueryService:
    def query(self, session_id: str, user_text: str):
        # Recuperar la sesión y guardar el mensaje del usuario en la base de datos
        session = ChatbotSession.objects.get(id=session_id)
        ChatbotMessage.objects.create(session=session, role="user", content=user_text)

        api_key = get_google_api_key()
        if not api_key:
            raise ValueError("La variable GOOGLE_API_KEY no está configurada.")

        # Configurar la conexión a la base de datos para PGVector de forma flexible
        db_key = "rag" if "rag" in settings.DATABASES else "default"
        db_settings = settings.DATABASES[db_key]
        
        contexto = ""
        total_activos = 0
        productos_mostrados = 0

        if db_settings["ENGINE"] == "django.db.backends.sqlite3":
            from django.db.models import Q
            from catalog.models import Product

            total_activos = Product.objects.filter(is_active=True).count()
            words = user_text.split()
            query_filter = Q()
            for word in words:
                if len(word) > 2:
                    query_filter |= Q(name__icontains=word) | Q(description__icontains=word)

            if query_filter:
                products = list(Product.objects.filter(query_filter)[:5])
            else:
                products = list(Product.objects.all()[:5])

            productos_mostrados = len(products)
            contexto = "\n\n".join([
                f"Producto: {p.name}\nSKU: {p.sku}\nPrecio: {p.price}\nDescripción: {p.description}\nCategoría: {p.category.name if p.category else 'General'}"
                for p in products
            ])
        else:
            import google.generativeai as genai
            from pgvector.django import L2Distance
            from catalog.models import Product as CatalogProduct

            total_activos = CatalogProduct.objects.filter(is_active=True).count()
            genai.configure(api_key=api_key)
            try:
                embed_result = genai.embed_content(
                    model="models/gemini-embedding-2",
                    content=user_text,
                    task_type="retrieval_query",
                    output_dimensionality=768
                )
                query_vector = embed_result['embedding']

                products = list(CatalogProduct.objects.filter(
                    embedding__isnull=False,
                    is_active=True,
                ).order_by(L2Distance('embedding', query_vector))[:5])

                productos_mostrados = len(products)
                contexto = "\n\n".join([
                    f"Producto: {p.name}\nSKU: {p.sku}\nPrecio: ${p.price}"
                    f"\nDescripción: {p.description[:150]}"
                    f"\nStock: {p.stock} {p.unit_of_sale} disponibles"
                    + (f"\nMarca: {p.brand}" if p.brand else "")
                    + (f"\nMaterial: {p.get_material_display()}" if p.material else "")
                    for p in products
                ])
            except Exception as e:
                contexto = ""
                print(f"Advertencia en búsqueda vectorial: {e}")

        internet_context = ""
        if not contexto.strip():
            internet_context = self._search_internet(user_text)

        # Construir el historial de mensajes para el modelo de lenguaje
        # Límite de 10 mensajes (5 turnos) para controlar el consumo de tokens
        MAX_HISTORY = 10
        db_messages = session.messages.order_by("created_at").values("role", "content")
        all_messages = list(db_messages)
        es_primer_mensaje = not any(m["role"] == "assistant" for m in all_messages)
        db_messages = all_messages[-MAX_HISTORY:]

        saludo_regla = (
            "Es el PRIMER mensaje de la sesión: podés saludar brevemente (ej. 'Hola,')."
            if es_primer_mensaje else
            "Ya hay mensajes previos en la sesión: NO saludes ni uses 'Hola' ni ningún saludo. Respondé directamente al punto."
        )

        messages: list[BaseMessage] = [
            SystemMessage(
                content=(
                    "Eres un Asesor Experto para un e-commerce de construcción y herramientas. "
                    "Tu objetivo es ayudar al cliente de forma amable, profesional y directa.\n\n"
                    f"REGLA DE SALUDO: {saludo_regla}\n\n"
                    "REGLA DE ORO: CLASIFICACIÓN DEL MENSAJE\n"
                    "Antes de responder, analiza qué está pidiendo el usuario y aplica SOLO las reglas del caso correspondiente:\n\n"
                    "--- CASO A: CONSULTA SIMPLE (Stock, Precios, Dudas puntuales) ---\n"
                    "Si el usuario pregunta si hay un producto, su precio, o características generales:\n"
                    "- Acción: Responde de manera natural, directa y concisa.\n"
                    "- Formato: Respuesta basada en el catálogo y una pregunta de cierre (ej. '¿Te ayudo con algo más?'). NO generes presupuestos ni guías de armado.\n\n"
                    "--- CASO B: CONSULTA EN DOMINIO SIN STOCK (Construcción/Herramientas pero no en catálogo) ---\n"
                    "Si la pregunta SI es sobre construcción, ferretería o herramientas, pero NO aparece en el catálogo:\n"
                    "- Acción: Responde con información general útil (definición, uso común, nombres alternativos), SIN inventar precios ni afirmar stock.\n"
                    "- Formato: La explicación general y una pregunta de cierre. Si corresponde, indica que no está en el catálogo o no hay stock.\n\n"
                    "--- CASO C: PROYECTOS DIY (Ej: 'Cómo construir un escritorio', 'Quiero hacer una pared') ---\n"
                    "Si el usuario pide ayuda para fabricar, armar o construir algo:\n"
                    "- Acción: Asume el rol de 'Asesor de Proyectos'.\n"
                    "- Estructura Obligatoria para este caso:\n"
                    "  1. INTRO: Pedí medidas si no las dio (sin saludar si no es el primer mensaje).\n"
                    "  2. MATERIALES: Lista separando lo que SÍ vendemos (con precio) de lo que NO vendemos. Usa guiones simples '-'.\n"
                    "  3. PRESUPUESTO: Suma total de los productos que sí tenemos.\n"
                    "  4. GUÍA: Paso a paso lógico del armado.\n\n"
                    "--- CASO D: FUERA DE CONTEXTO (Ej: Recetas de cocina, política, programación) ---\n"
                    "Si la pregunta no tiene relación con construcción, ferretería o proyectos de hogar:\n"
                    "- Acción: Negación educada.\n"
                    "- Formato: 'Soy un asistente especializado en materiales de construcción y herramientas. No puedo ayudarte con [tema del usuario], pero si necesitas materiales para tu hogar o taller, estoy aquí para ayudarte.'\n\n"
                    "REGLAS DE FORMATO GENERALES (Para todos los casos):\n"
                    "1. NO uses Markdown. Están prohibidos los asteriscos (*), negritas (**), cursivas o numerales (#).\n"
                    "2. Usa DOBLE salto de línea para separar los párrafos.\n"
                    "3. Cuando uses el catálogo, respeta el contexto de productos. NO inventes productos ni precios.\n"
                    "4. Si la consulta es del dominio pero no está en el catálogo, puedes responder con conocimiento general sin precios.\n"
                    "5. Si hay contexto de internet, úsalo SOLO cuando el catálogo esté vacío y la consulta sea simple sobre un producto. Aclara que el stock no está confirmado.\n"
                    "6. Si usas internet, NO digas que buscaste en internet; solo responde con la informacion.\n\n"
                    f"--- CONTEXTO DE PRODUCTOS DISPONIBLES ---\n"
                    f"(El catálogo tiene {total_activos} productos activos en total. "
                    f"Se muestran los {productos_mostrados} más relevantes para esta consulta según búsqueda semántica. "
                    f"Si el cliente pregunta por algo que no aparece aquí, puede existir en el catálogo pero no ser relevante para su búsqueda actual.)\n\n"
                    f"{contexto}\n\n"
                    f"--- CONTEXTO DE INTERNET (si aplica) ---\n{internet_context}"
                )
            ),
        ]

        # Agregar los mensajes anteriores de la sesión al historial
        for msg in db_messages:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            else:
                messages.append(AIMessage(content=msg["content"]))

        # Configurar el modelo de lenguaje de Google Gemini
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash", api_key=api_key
        )

        def generate():
            # Generar la respuesta del asistente utilizando el modelo de lenguaje
            completed_response = ""
            
            try:
                for chunk in llm.stream(messages):
                    content_piece = chunk.content

                    # Acumular la respuesta completa a medida que se reciben los fragmentos
                    if isinstance(content_piece, str):
                        completed_response += content_piece

                    payload = json.dumps({"response": chunk.content})
                    yield f"data: {payload}\n\n"
                    
            except Exception as e:
                # Loggear el error real en la consola del backend
                print(f"Error en API de Gemini: {e}")
                
                # Si ocurre un error de API (ej. límite de cuota 429), enviarlo como respuesta amigable
                error_msg = "\n\nLo siento, ha ocurrido un error al comunicarme con mi servidor (posiblemente la cuota de la API se agotó). Por favor, intenta de nuevo más tarde."
                completed_response += error_msg
                payload = json.dumps({"response": error_msg})
                yield f"data: {payload}\n\n"

            # Guardar la respuesta del asistente en la base de datos
            ChatbotMessage.objects.create(
                session=session, role="assistant", content=completed_response
            )

            yield "data: [DONE]\n\n"

        return generate()

    def _search_internet(self, user_text: str) -> str:
        try:
            search = DuckDuckGoSearchAPIWrapper()
            results = search.results(user_text, max_results=3)
        except Exception:
            return ""

        lines = []
        for index, result in enumerate(results, start=1):
            title = result.get("title", "").strip()
            snippet = result.get("snippet", "").strip()
            link = result.get("link", "").strip()
            if not title and not snippet and not link:
                continue
            parts = []
            if title:
                parts.append(title)
            if snippet:
                parts.append(snippet)
            if link:
                parts.append(f"Fuente: {link}")
            lines.append(f"Resultado {index}: " + ". ".join(parts))
        return "\n".join(lines)
